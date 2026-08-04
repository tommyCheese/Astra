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

from app.agent_profile import AgentProfile
from app.runtime_configuration import RuntimeConfiguration
from app.runtime_dependency_policy import CORE_DEPENDENCIES, normalize_dependencies
from app.runtime_profile_persistence import RuntimeProfilePersistence
from app.sandbox.runtime import sanitize_log

MANAGED_IMAGE = re.compile(r"^astra-data-viz:(?:build-[0-9a-f-]+|custom-[0-9a-f]+)$")


class RuntimeProfileService:
    def __init__(self, settings, *, recover_interrupted: bool = False, session_factory=None):
        self.settings, self.path, self.tasks = settings, Path(settings.runtime_profile_path), {}
        self.session_factory = session_factory
        self.persistence = RuntimeProfilePersistence(session_factory)
        self.recovered_staging_images = []
        self.configuration = RuntimeConfiguration(settings, self.path)
        if recover_interrupted:
            self._recover_interrupted_build()

    def _read_persisted(self):
        return self.configuration.read_persisted()

    def active_agent_profile(self) -> AgentProfile:
        return self.configuration.active_profile

    def update_agent_profile(self, documents):
        return self.configuration.update_agent_profile(documents)

    def reset_agent_profile(self):
        return self.configuration.reset_agent_profile()

    def memory_settings(self):
        return self.configuration.memory_settings()

    def update_memory_settings(self, value):
        return self.configuration.update_memory_settings(value)

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
        await self._load_database_state()
        recovered = list(dict.fromkeys(self.recovered_staging_images))
        self.recovered_staging_images = []
        for build_id, image in recovered:
            await self._remove_managed_image(build_id, image)
        cleanup_id = recovered[-1][0] if recovered else "startup"
        await self._prune_images(cleanup_id)

    async def _load_database_state(self):
        state, recovered = await self.persistence.load(self.read())
        self.recovered_staging_images.extend(recovered)
        self.write(state)
        await self.persistence.persist(state)

    async def _persist_database_state(self, state):
        await self.persistence.persist(state)

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
        state["agent_profile"] = self.configuration.profile_view(state)
        state["memory_settings"] = self.memory_settings()
        return state

    def write(self, value, *, persist_memory_settings: bool = False):
        self.configuration.write_state(value, persist_memory_settings=persist_memory_settings)

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
            "dependencies": deps,
            "dependency_digest": digest,
        }
        self.write(state)
        await self._persist_database_state(state)
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
            return await self._update_build(
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
            state = await self._update_build(
                build_id,
                status="cancelled",
                phase="已取消",
                log="构建已由用户取消",
                image=None,
            )
        return state

    async def _update_build(self, build_id, **values):
        state = self.read()
        if (state.get("build") or {}).get("id") != build_id:
            return state
        state["build"].update(values)
        self.write(state)
        await self._persist_database_state(state)
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
                await self._update_build(
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
            await self._persist_database_state(state)
        return cleanup_failed

    async def _build(self, build_id, deps, digest):
        await self._update_build(
            build_id,
            status="building",
            phase="准备构建环境",
            progress=5,
            log="正在解析依赖并准备隔离环境",
        )
        staging_image = f"astra-data-viz:build-{build_id}"
        try:
            await self._execute_build(build_id, deps, digest, staging_image)
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                await self._remove_managed_image(build_id, staging_image)
            await self._update_build(
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
            await self._update_build(
                build_id,
                status="failed",
                phase="构建失败",
                log=sanitize_log(str(exc) or "构建超时"),
                image=None,
            )

    async def _execute_build(self, build_id, deps, digest, staging_image):
        has_unpinned = any(not item["version"] for item in deps)
        image = staging_image
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
                smoke_code = (
                    f"import importlib.metadata as m; [m.version(name) for name in {package_names}]"
                )
            await self._update_build(
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
            await self._activate_built_image(
                build_id, image, digest, resolved_dependencies, staging_image
            )

    async def _activate_built_image(
        self, build_id, source_image, digest, dependencies, cleanup_image
    ) -> None:
        image = f"astra-data-viz:custom-{digest}"
        await self._run_with_progress(
            build_id,
            [self.settings.docker_binary, "tag", source_image, image],
            phase="固定依赖版本",
            start=98,
            end=100,
            display_output=False,
        )
        state = self.read()
        image_record = {
            "image": image,
            "dependency_digest": digest,
            "dependencies": dependencies,
            "activated_at": datetime.now(timezone.utc).isoformat(),
        }
        images = [item for item in state.get("images", []) if item.get("image") != image]
        state.update(
            dependencies=dependencies,
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
            dependencies=dependencies,
            dependency_digest=digest,
        )
        self.write(state)
        await self._persist_database_state(state)
        cleanup_failed = not await self._remove_managed_image(build_id, cleanup_image)
        cleanup_failed = await self._prune_images(build_id) or cleanup_failed
        if cleanup_failed:
            await self._update_build(
                build_id, log="构建与导入验证成功；部分旧镜像暂未清理，将在后续构建重试"
            )
