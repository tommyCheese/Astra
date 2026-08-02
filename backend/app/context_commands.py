from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.automation_commands import AutomationCommandService
from app.conversation_context import ConversationContextManager
from app.core.config import Settings
from app.core.errors import ResourceError, ValidationError
from app.db.models import TaskRecord
from app.schemas.agent import EXECUTABLE_SUBAGENT_COHORTS


@dataclass(frozen=True)
class SystemCommandDefinition:
    name: str
    description: str
    effect: str
    argument_mode: str = "none"
    usage: str = ""
    available: bool = True
    side_effect: str = "write"
    execution_mode: str = "host"
    unavailable_reason: str | None = None

    def view(self) -> dict[str, object]:
        return {
            "name": self.name,
            "command": f"/{self.name}",
            "description": self.description,
            "effect": self.effect,
            "argument_mode": self.argument_mode,
            "usage": self.usage or f"/{self.name}",
            "available": self.available,
            "side_effect": self.side_effect,
            "execution_mode": self.execution_mode,
            "unavailable_reason": self.unavailable_reason,
        }


SYSTEM_COMMANDS = (
    SystemCommandDefinition(
        name="compact",
        description="整理较早的对话，保留近期内容和完整记录",
        effect="compact_context",
    ),
    SystemCommandDefinition(
        name="clear",
        description="让模型从当前消息重新开始，完整记录仍会保留",
        effect="clear_context",
    ),
    SystemCommandDefinition(
        name="schedule",
        description="创建、查看或管理当前对话的定时任务",
        effect="manage_schedules",
        argument_mode="required",
        usage="/schedule list|show|create|pause|resume|run|delete …",
        side_effect="mixed",
    ),
    SystemCommandDefinition(
        name="heartbeat",
        description="查看、启用或关闭当前对话的 heartbeat",
        effect="manage_heartbeat",
        argument_mode="required",
        usage="/heartbeat status|on|off|run …",
        side_effect="mixed",
    ),
)


def list_system_commands(settings: Settings | None = None) -> list[dict[str, object]]:
    commands = list(SYSTEM_COMMANDS)
    enabled = bool(
        settings
        and settings.agent_subagent_execution_enabled
        and not settings.agent_subagent_kill_switch
        and settings.agent_subagent_rollout_cohort in EXECUTABLE_SUBAGENT_COHORTS
    )
    commands.append(
        SystemCommandDefinition(
            name="subagent",
            description="使用 Astra Swarm 并发子 Agent 完成指定任务",
            effect="start_subagent_run",
            argument_mode="required",
            usage="/subagent <任务>",
            side_effect="write",
            execution_mode="run",
            available=enabled,
            unavailable_reason=(
                None if enabled else "当前部署尚未启用受治理子 Agent 执行。"
            ),
        )
    )
    return [definition.view() for definition in commands]


async def execute_system_command(
    manager: ConversationContextManager,
    task: TaskRecord,
    command: str,
    *,
    arguments: str = "",
    session: AsyncSession | None = None,
    settings: Settings | None = None,
) -> tuple[str, dict[str, object]]:
    definition = next((item for item in SYSTEM_COMMANDS if item.name == command), None)
    if definition is None:
        raise ResourceError("SYSTEM_COMMAND_NOT_FOUND", "找不到这个快捷操作。")
    if definition.argument_mode == "required":
        if not arguments.strip():
            raise ValidationError(
                "SYSTEM_COMMAND_ARGUMENTS_REQUIRED",
                f"此命令需要参数。用法：{definition.usage}",
                {"usage": definition.usage, "command": f"/{command}"},
            )
        if session is None or settings is None:
            raise RuntimeError("Parameterized command dependencies are unavailable")
        automation = AutomationCommandService(session, settings)
        if command == "schedule":
            return await automation.execute_schedule(task, arguments)
        return await automation.execute_heartbeat(task, arguments)
    if arguments.strip():
        raise ValidationError(
            "SYSTEM_COMMAND_USAGE_INVALID",
            f"/{command} 不接受参数。",
            {"usage": definition.usage, "command": f"/{command}"},
        )
    if command == "compact":
        details = await manager.compact(task)
        return "已整理较早的对话，完整记录仍保留。", details
    details = await manager.clear(task)
    return "模型将从当前消息重新开始，完整记录仍保留。", details
