import asyncio
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
        if not NAME.fullmatch(name) or (version and not VERSION.fullmatch(version)) or normalized in PROTECTED:
            raise ValueError(f"不允许的依赖：{name}")
        if normalized in seen:
            raise ValueError(f"依赖重复：{name}")
        seen.add(normalized)
        result.append({"name": normalized, "version": version})
    return sorted(result, key=lambda value: value["name"])


class RuntimeProfileService:
    def __init__(self, settings):
        self.settings, self.path, self.tasks = settings, Path(settings.runtime_profile_path), {}

    def read(self):
        if self.path.exists():
            state = json.loads(self.path.read_text())
        else:
            state = {"dependencies": [], "active_image": self.settings.sandbox_runtime_image, "dependency_digest": self.settings.sandbox_runtime_lock_digest, "build": None}
        state["core_dependencies"] = CORE_DEPENDENCIES
        return state

    def write(self, value):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
        temporary.replace(self.path)

    async def start(self, dependencies):
        deps = normalize_dependencies(dependencies)
        state = self.read()
        if (state.get("build") or {}).get("status") in {"queued", "building"}:
            raise RuntimeError("已有构建正在进行")
        build_id = str(uuid.uuid4())
        digest = hashlib.sha256(json.dumps(deps, sort_keys=True).encode()).hexdigest()[:16]
        state["dependencies"] = deps
        state["build"] = {"id": build_id, "status": "queued", "log": "等待构建", "image": None}
        self.write(state)
        task = asyncio.create_task(self._build(build_id, deps, digest))
        self.tasks[build_id] = task
        task.add_done_callback(lambda _: self.tasks.pop(build_id, None))
        return self.read()

    async def _build(self, build_id, deps, digest):
        state = self.read()
        state["build"].update(status="building", log="正在解析并安装依赖")
        self.write(state)
        image = f"astra-data-viz:custom-{digest}"
        try:
            with tempfile.TemporaryDirectory(prefix="astra-runtime-build-") as root:
                requirements = " ".join(
                    f"{item['name']}=={item['version']}" if item["version"] else item["name"]
                    for item in deps
                )
                install = f"RUN uv pip install --python /opt/astra/runtime/.venv/bin/python {requirements}\n" if requirements else ""
                Path(root, "Dockerfile").write_text(
                    f"FROM {self.settings.sandbox_runtime_image}\nUSER root\n{install}USER 65532:65532\n"
                )
                process = await asyncio.create_subprocess_exec(self.settings.docker_binary, "build", "--network", "default", "-t", image, root, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
                await asyncio.wait_for(process.communicate(), self.settings.runtime_build_timeout_seconds)
                if process.returncode:
                    raise RuntimeError("镜像构建失败")
                package_names = json.dumps([item["name"] for item in deps])
                smoke_code = f"import importlib.metadata as m; [m.version(name) for name in {package_names}]"
                smoke = await asyncio.create_subprocess_exec(self.settings.docker_binary, "run", "--rm", "--network", "none", image, "/opt/astra/runtime/.venv/bin/python", "-c", smoke_code, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
                await asyncio.wait_for(smoke.communicate(), self.settings.runtime_build_timeout_seconds)
                if smoke.returncode:
                    raise RuntimeError("依赖导入验证失败")
                state = self.read()
                state.update(active_image=image, dependency_digest=digest)
                state["build"].update(status="succeeded", log="构建与导入验证成功", image=image)
                self.write(state)
        except Exception as exc:
            state = self.read()
            state["build"].update(status="failed", log=sanitize_log(str(exc)), image=None)
            self.write(state)
