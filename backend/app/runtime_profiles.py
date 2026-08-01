import asyncio
import contextlib
import hashlib
import json
import re
import tempfile
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.agent_profile import AgentProfile, AgentProfileLoader
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
MANAGED_IMAGE = re.compile(r"^astra-data-viz:(?:build-[0-9a-f-]+|custom-[0-9a-f]+)$")
MEMORY_SETTING_BOUNDS = {
    "retrieval_max_items": (0, 50),
    "retrieval_max_tokens": (0, 32_000),
    "retrieval_min_confidence": (0.0, 1.0),
    "retrieval_min_score": (0.0, 1.0),
    "autodream_scan_seconds": (60, 604_800),
    "autodream_min_candidates": (2, 100),
}


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
        self.recovered_staging_images = []
        self._packaged_agent_profile = AgentProfileLoader().load()
        self._active_agent_profile = self._load_agent_profile(self._read_persisted())
        persisted_memory = self._read_persisted().get("memory_settings")
        if persisted_memory is not None:
            self._apply_memory_settings(self._normalize_memory_settings(persisted_memory))
        if recover_interrupted:
            self._recover_interrupted_build()

    def _read_persisted(self):
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text())

    def _load_agent_profile(self, state) -> AgentProfile:
        value = state.get("agent_profile")
        if value is None:
            return self._packaged_agent_profile
        documents = value.get("documents") if isinstance(value, dict) else None
        if not isinstance(documents, dict):
            raise ValueError("Agent Profile 运行时配置无效")
        return AgentProfileLoader().load(documents)

    @staticmethod
    def _agent_profile_documents(profile: AgentProfile):
        return {document.name: document.content for document in profile.manifest.documents}

    def _agent_profile_view(self, state):
        source = "user" if state.get("agent_profile") is not None else "default"
        profile = self._active_agent_profile
        return {
            "source": source,
            "version": profile.manifest.version,
            "documents": self._agent_profile_documents(profile),
        }

    def active_agent_profile(self) -> AgentProfile:
        return self._active_agent_profile

    def update_agent_profile(self, documents):
        profile = AgentProfileLoader().load(documents)
        state = self._read_persisted()
        state["agent_profile"] = {
            "documents": self._agent_profile_documents(profile),
        }
        self.write(state)
        self._active_agent_profile = profile
        return self._agent_profile_view(state)

    def reset_agent_profile(self):
        state = self._read_persisted()
        state.pop("agent_profile", None)
        self.write(state)
        self._active_agent_profile = self._packaged_agent_profile
        return self._agent_profile_view(state)

    def memory_settings(self):
        return {
            "write_enabled": self.settings.agent_memory_write_enabled,
            "recall_enabled": self.settings.agent_memory_cross_session_enabled,
            "retrieval_max_items": self.settings.agent_memory_retrieval_max_items,
            "retrieval_max_tokens": self.settings.agent_memory_retrieval_max_tokens,
            "retrieval_min_confidence": self.settings.agent_memory_retrieval_min_confidence,
            "retrieval_min_score": self.settings.agent_memory_retrieval_min_score,
            "autodream_enabled": self.settings.agent_memory_autodream_enabled,
            "autodream_scan_seconds": self.settings.agent_memory_autodream_scan_seconds,
            "autodream_min_candidates": self.settings.agent_memory_autodream_min_candidates,
        }

    def _normalize_memory_settings(self, value):
        if not isinstance(value, dict):
            raise ValueError("记忆运行设置必须是对象")
        value = dict(value)
        required = set(self.memory_settings())
        if set(value) != required:
            raise ValueError("记忆运行设置字段不完整")
        if any(
            not isinstance(value[field], bool)
            for field in ("write_enabled", "recall_enabled", "autodream_enabled")
        ):
            raise ValueError("记忆运行开关必须是布尔值")
        normalized = {
            "write_enabled": value["write_enabled"],
            "recall_enabled": value["recall_enabled"],
            "autodream_enabled": value["autodream_enabled"],
        }
        integer_fields = {
            "retrieval_max_items",
            "retrieval_max_tokens",
            "autodream_scan_seconds",
            "autodream_min_candidates",
        }
        for field, (minimum, maximum) in MEMORY_SETTING_BOUNDS.items():
            raw = value[field]
            if field in integer_fields:
                if not isinstance(raw, int) or isinstance(raw, bool):
                    raise ValueError(f"记忆运行设置 {field} 必须是整数")
            elif not isinstance(raw, (int, float)) or isinstance(raw, bool):
                raise ValueError(f"记忆运行设置 {field} 必须是数字")
            if not minimum <= raw <= maximum:
                raise ValueError(f"记忆运行设置 {field} 超出允许范围")
            normalized[field] = raw
        return normalized

    def _apply_memory_settings(self, value):
        self.settings.agent_memory_write_enabled = value["write_enabled"]
        self.settings.agent_memory_cross_session_enabled = value["recall_enabled"]
        self.settings.agent_memory_retrieval_max_items = value["retrieval_max_items"]
        self.settings.agent_memory_retrieval_max_tokens = value["retrieval_max_tokens"]
        self.settings.agent_memory_retrieval_min_confidence = value["retrieval_min_confidence"]
        self.settings.agent_memory_retrieval_min_score = value["retrieval_min_score"]
        self.settings.agent_memory_autodream_enabled = value["autodream_enabled"]
        self.settings.agent_memory_autodream_scan_seconds = value["autodream_scan_seconds"]
        self.settings.agent_memory_autodream_min_candidates = value["autodream_min_candidates"]

    def update_memory_settings(self, value):
        normalized = self._normalize_memory_settings(value)
        state = self._read_persisted()
        state["memory_settings"] = normalized
        self.write(state, persist_memory_settings=True)
        self._apply_memory_settings(normalized)
        return self.memory_settings()

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
        build_id = build.get("id")
        if build_id:
            self.recovered_staging_images.append((build_id, f"astra-data-viz:build-{build_id}"))
        build.update(
            status="cancelled",
            phase="构建已中断",
            log="Astra 已重新启动，原构建任务已停止",
            image=None,
        )
        self.write(state)

    async def startup(self):
        recovered = self.recovered_staging_images
        self.recovered_staging_images = []
        for build_id, image in recovered:
            await self._remove_managed_image(build_id, image)
        cleanup_id = recovered[-1][0] if recovered else "startup"
        await self._prune_images(cleanup_id)

    def read(self):
        state = self._read_persisted()
        state.setdefault("dependencies", [])
        state.setdefault("active_image", self.settings.sandbox_runtime_image)
        state.setdefault("dependency_digest", self.settings.sandbox_runtime_lock_digest)
        state.setdefault("build", None)
        state.setdefault("images", [])
        images = state.setdefault("images", [])
        active_image = state.get("active_image", self.settings.sandbox_runtime_image)
        if active_image.startswith("astra-data-viz:custom-") and not any(
            item.get("image") == active_image for item in images
        ):
            images.append(
                {
                    "image": active_image,
                    "dependency_digest": state.get("dependency_digest", ""),
                    "dependencies": state.get("dependencies", []),
                    "activated_at": None,
                }
            )
        state["core_dependencies"] = [item.copy() for item in CORE_DEPENDENCIES]
        state["image_policy"] = {
            "keep_recent": max(0, self.settings.runtime_image_keep_recent),
            "retention_days": max(0, self.settings.runtime_image_retention_days),
        }
        state["agent_profile"] = self._agent_profile_view(state)
        state["memory_settings"] = self.memory_settings()
        return state

    def write(self, value, *, persist_memory_settings: bool = False):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        persisted = {
            key: item
            for key, item in value.items()
            if key not in {"core_dependencies", "image_policy"}
        }
        agent_profile = persisted.get("agent_profile")
        if isinstance(agent_profile, dict) and "source" in agent_profile:
            if agent_profile.get("source") == "user":
                persisted["agent_profile"] = {
                    "documents": agent_profile.get("documents", {}),
                }
            else:
                persisted.pop("agent_profile", None)
        if not persist_memory_settings and "memory_settings" not in self._read_persisted():
            persisted.pop("memory_settings", None)
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
                log="构建状态已取消；原构建任务已结束",
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

    async def _run_with_progress(
        self,
        build_id,
        command,
        *,
        phase,
        start,
        end,
        capture_output=False,
        display_output=True,
    ):
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        async def consume_output():
            line_count = 0
            recent_output = deque(maxlen=8)
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                latest = sanitize_log(line.decode(errors="replace").strip(), limit=600)
                if not latest:
                    continue
                recent_output.append(latest)
                if not display_output:
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
            returncode = await process.wait()
            if returncode:
                detail = "\n".join(recent_output) or "命令未返回错误详情"
                raise RuntimeError(f"{phase}失败（退出码 {returncode}）：\n{detail}")
            return "\n".join(recent_output) if capture_output else returncode

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

    async def _remove_managed_image(self, build_id, image):
        if not MANAGED_IMAGE.fullmatch(image):
            return False
        try:
            process = await asyncio.create_subprocess_exec(
                self.settings.docker_binary,
                "image",
                "rm",
                image,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            output, _ = await asyncio.wait_for(process.communicate(), 15)
        except (OSError, asyncio.TimeoutError):
            if "process" in locals() and process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 2)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
            return False
        detail = sanitize_log(output.decode(errors="replace"))
        return process.returncode == 0 or "No such image" in detail

    @staticmethod
    def _activated_at(value):
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None

    async def _prune_images(self, build_id):
        state = self.read()
        active_image = state.get("active_image")
        inactive = sorted(
            (item for item in state.get("images", []) if item.get("image") != active_image),
            key=lambda item: item.get("activated_at") or "",
            reverse=True,
        )
        keep_recent = max(0, self.settings.runtime_image_keep_recent)
        protected = {item.get("image") for item in inactive[:keep_recent]}
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=max(0, self.settings.runtime_image_retention_days)
        )
        removed = set()
        cleanup_failed = False
        for item in inactive:
            image = item.get("image", "")
            activated_at = self._activated_at(item.get("activated_at"))
            expired = activated_at is None or activated_at <= cutoff
            if image in protected and not expired:
                continue
            if await self._remove_managed_image(build_id, image):
                removed.add(image)
            else:
                cleanup_failed = True
        if removed:
            state = self.read()
            state["images"] = [
                item for item in state.get("images", []) if item.get("image") not in removed
            ]
            self.write(state)
        return cleanup_failed

    async def _build(self, build_id, deps, digest):
        self._update_build(
            build_id,
            status="building",
            phase="准备构建环境",
            progress=5,
            log="正在解析依赖并准备隔离环境",
        )
        has_unpinned = any(not item["version"] for item in deps)
        staging_image = f"astra-data-viz:build-{build_id}"
        image = staging_image
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
                    f"FROM {self.settings.sandbox_runtime_image}\n"
                    f'LABEL io.astra.runtime.managed="true" io.astra.runtime.build-id="{build_id}"\n'
                    f"USER root\n{install}USER 65532:65532\n"
                )
                build_options = ["--no-cache"] if has_unpinned else []
                await self._run_with_progress(
                    build_id,
                    [
                        self.settings.docker_binary,
                        "build",
                        *build_options,
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
                package_names = json.dumps([item["name"] for item in deps])
                if has_unpinned:
                    smoke_code = (
                        "import importlib.metadata as m, json; "
                        f"names={package_names}; "
                        "print('ASTRA_DEPENDENCIES=' + json.dumps({name: m.version(name) "
                        "for name in names}, sort_keys=True))"
                    )
                else:
                    smoke_code = f"import importlib.metadata as m; [m.version(name) for name in {package_names}]"
                self._update_build(
                    build_id,
                    phase="验证依赖导入",
                    progress=85,
                    log="环境构建完成，正在验证依赖",
                )
                verification_output = await self._run_with_progress(
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
                    capture_output=has_unpinned,
                    display_output=False,
                )
                resolved_dependencies = deps
                if has_unpinned:
                    prefix = "ASTRA_DEPENDENCIES="
                    version_line = next(
                        (
                            line
                            for line in reversed(verification_output.splitlines())
                            if line.startswith(prefix)
                        ),
                        None,
                    )
                    if version_line is None:
                        raise RuntimeError("依赖验证成功，但未能读取实际安装版本")
                    installed_versions = json.loads(version_line.removeprefix(prefix))
                    resolved_dependencies = [
                        {
                            "name": item["name"],
                            "version": item["version"] or installed_versions[item["name"]],
                        }
                        for item in deps
                    ]
                    digest = hashlib.sha256(
                        json.dumps(resolved_dependencies, sort_keys=True).encode()
                    ).hexdigest()[:16]
                resolved_image = f"astra-data-viz:custom-{digest}"
                await self._run_with_progress(
                    build_id,
                    [self.settings.docker_binary, "tag", image, resolved_image],
                    phase="固定依赖版本",
                    start=98,
                    end=100,
                    display_output=False,
                )
                image = resolved_image
                state = self.read()
                image_record = {
                    "image": image,
                    "dependency_digest": digest,
                    "dependencies": resolved_dependencies,
                    "activated_at": datetime.now(timezone.utc).isoformat(),
                }
                images = [item for item in state.get("images", []) if item.get("image") != image]
                state.update(
                    dependencies=resolved_dependencies,
                    active_image=image,
                    dependency_digest=digest,
                    images=[image_record, *images],
                )
                state["build"].update(
                    status="succeeded",
                    phase="构建完成",
                    progress=100,
                    log="构建与导入验证成功",
                    image=image,
                )
                self.write(state)
                cleanup_failed = not await self._remove_managed_image(build_id, staging_image)
                cleanup_failed = await self._prune_images(build_id) or cleanup_failed
                if cleanup_failed:
                    self._update_build(
                        build_id,
                        log="构建与导入验证成功；部分旧镜像暂未清理，将在后续构建重试",
                    )
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                await self._remove_managed_image(build_id, staging_image)
            self._update_build(
                build_id,
                status="cancelled",
                phase="已取消",
                log="构建已由用户取消",
                image=None,
            )
            raise
        except Exception as exc:
            with contextlib.suppress(Exception):
                await self._remove_managed_image(build_id, staging_image)
            self._update_build(
                build_id,
                status="failed",
                phase="构建失败",
                log=sanitize_log(str(exc) or "构建超时"),
                image=None,
            )
