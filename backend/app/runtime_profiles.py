import asyncio
import contextlib
import hashlib
import json
import re
import tempfile
import uuid
from pathlib import Path

from app.sandbox.runtime import sanitize_log

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
VERSION = re.compile(r"^[0-9]+(?:\.[0-9A-Za-z]+)*(?:[-+][0-9A-Za-z.-]+)?$")
CORE_DEPENDENCIES = [
    {"name": "matplotlib", "version": "3.10.3"},
    {"name": "numpy", "version": "2.2.6"},
    {"name": "pandas", "version": "2.2.3"},
    {"name": "pillow", "version": "11.2.1"},
    {"name": "pyarrow", "version": "20.0.0"},
    {"name": "scipy", "version": "1.15.3"},
    {"name": "seaborn", "version": "0.13.2"},
]
PROTECTED = {item["name"] for item in CORE_DEPENDENCIES} | {"echarts", "playwright"}


def normalize_dependencies(values):
    if len(values) > 32:
        raise ValueError("最多允许 32 个依赖")
    result, seen = [], set()
    for item in values:
        name, version = str(item.get("name", "")).strip(), str(item.get("version", "")).strip()
        normalized = re.sub(r"[-_.]+", "-", name).lower()
        if (
            not NAME.fullmatch(name)
            or (version and not VERSION.fullmatch(version))
            or normalized in PROTECTED
        ):
            raise ValueError(f"不允许的依赖：{name}")
        if normalized in seen:
            raise ValueError(f"依赖重复：{name}")
        seen.add(normalized)
        result.append({"name": normalized, "version": version})
    return sorted(result, key=lambda value: value["name"])


class RuntimeProfileService:
    def __init__(self, settings, *, recover_interrupted: bool = False):
        self.settings, self.path, self.tasks = settings, Path(settings.runtime_profile_path), {}
        if recover_interrupted:
            self._recover_interrupted_build()

    def _recover_interrupted_build(self):
        if not self.path.exists():
            return
        try:
            state = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return
        build = state.get("build") or {}
        if build.get("status") not in {"queued", "building"}:
            return
        build.update(
            status="cancelled",
            phase="构建已中断",
            log="后端服务重启，原构建任务已停止",
            image=None,
        )
        self.write(state)

    def read(self):
        if self.path.exists():
            state = json.loads(self.path.read_text())
        else:
            state = {
                "dependencies": [],
                "active_image": self.settings.sandbox_runtime_image,
                "dependency_digest": self.settings.sandbox_runtime_lock_digest,
                "build": None,
            }
        state["core_dependencies"] = [item.copy() for item in CORE_DEPENDENCIES]
        return state

    def write(self, value):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        persisted = {key: item for key, item in value.items() if key != "core_dependencies"}
        temporary.write_text(json.dumps(persisted, ensure_ascii=False, indent=2))
        temporary.replace(self.path)

    async def start(self, dependencies):
        deps = normalize_dependencies(dependencies)
        state = self.read()
        if (state.get("build") or {}).get("status") in {"queued", "building"}:
            raise RuntimeError("已有构建正在进行")
        build_id = str(uuid.uuid4())
        digest = hashlib.sha256(json.dumps(deps, sort_keys=True).encode()).hexdigest()[:16]
        state["dependencies"] = deps
        state["build"] = {
            "id": build_id,
            "status": "queued",
            "phase": "等待构建",
            "progress": 0,
            "log": "等待构建",
            "image": None,
        }
        self.write(state)
        task = asyncio.create_task(self._build(build_id, deps, digest))
        self.tasks[build_id] = task
        task.add_done_callback(lambda _: self.tasks.pop(build_id, None))
        return self.read()

    async def shutdown(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def cancel(self, build_id):
        state = self.read()
        build = state.get("build") or {}
        if build.get("id") != build_id or build.get("status") not in {"queued", "building"}:
            raise RuntimeError("当前构建已结束或不存在")
        task = self.tasks.get(build_id)
        if task is None:
            return self._update_build(
                build_id,
                status="cancelled",
                phase="已取消",
                log="构建状态已取消；后台任务已不在当前进程中",
                image=None,
            )
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        state = self.read()
        if (state.get("build") or {}).get("status") in {"queued", "building"}:
            state = self._update_build(
                build_id,
                status="cancelled",
                phase="已取消",
                log="构建已由用户取消",
                image=None,
            )
        return state

    def _update_build(self, build_id, **values):
        state = self.read()
        if (state.get("build") or {}).get("id") != build_id:
            return state
        state["build"].update(values)
        self.write(state)
        return state

    async def _run_with_progress(self, build_id, command, *, phase, start, end):
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        async def consume_output():
            line_count = 0
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                latest = sanitize_log(line.decode(errors="replace").strip(), limit=600)
                if not latest:
                    continue
                line_count += 1
                progress = min(end - 1, start + line_count)
                self._update_build(
                    build_id,
                    status="building",
                    phase=phase,
                    progress=progress,
                    log=latest,
                )
            return await process.wait()

        try:
            return await asyncio.wait_for(
                consume_output(), self.settings.runtime_build_timeout_seconds
            )
        except BaseException:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
            raise

    async def _build(self, build_id, deps, digest):
        self._update_build(
            build_id,
            status="building",
            phase="准备构建环境",
            progress=5,
            log="正在解析依赖并生成 Docker build context",
        )
        image = f"astra-data-viz:custom-{digest}"
        try:
            with tempfile.TemporaryDirectory(prefix="astra-runtime-build-") as root:
                requirements = " ".join(
                    f"{item['name']}=={item['version']}" if item["version"] else item["name"]
                    for item in deps
                )
                install = (
                    f"RUN uv pip install --python /opt/astra/runtime/.venv/bin/python {requirements}\n"
                    if requirements
                    else ""
                )
                Path(root, "Dockerfile").write_text(
                    f"FROM {self.settings.sandbox_runtime_image}\nUSER root\n{install}USER 65532:65532\n"
                )
                returncode = await self._run_with_progress(
                    build_id,
                    [
                        self.settings.docker_binary,
                        "build",
                        "--progress",
                        "plain",
                        "--network",
                        "default",
                        "-t",
                        image,
                        root,
                    ],
                    phase="构建镜像并安装依赖",
                    start=10,
                    end=82,
                )
                if returncode:
                    raise RuntimeError("镜像构建失败")
                package_names = json.dumps([item["name"] for item in deps])
                smoke_code = (
                    f"import importlib.metadata as m; [m.version(name) for name in {package_names}]"
                )
                self._update_build(
                    build_id,
                    phase="验证依赖导入",
                    progress=85,
                    log="镜像构建完成，正在断网验证依赖",
                )
                returncode = await self._run_with_progress(
                    build_id,
                    [
                        self.settings.docker_binary,
                        "run",
                        "--rm",
                        "--network",
                        "none",
                        image,
                        "/opt/astra/runtime/.venv/bin/python",
                        "-c",
                        smoke_code,
                    ],
                    phase="验证依赖导入",
                    start=85,
                    end=98,
                )
                if returncode:
                    raise RuntimeError("依赖导入验证失败")
                state = self.read()
                state.update(active_image=image, dependency_digest=digest)
                state["build"].update(
                    status="succeeded",
                    phase="构建完成",
                    progress=100,
                    log="构建与导入验证成功",
                    image=image,
                )
                self.write(state)
        except asyncio.CancelledError:
            self._update_build(
                build_id,
                status="cancelled",
                phase="已取消",
                log="构建已由用户取消",
                image=None,
            )
            raise
        except Exception as exc:
            self._update_build(
                build_id,
                status="failed",
                phase="构建失败",
                log=sanitize_log(str(exc) or "构建超时"),
                image=None,
            )
