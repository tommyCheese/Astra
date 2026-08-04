from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.automation_commands import AutomationCommandService
from app.conversation_context import ConversationContextManager
from app.core.config import Settings
from app.core.errors import ResourceError, ValidationError
from app.db.model_base import utc_now
from app.db.models.conversations import TaskRecord
from app.schemas.agent.run_policy import EXECUTABLE_SUBAGENT_COHORTS


@dataclass(frozen=True)
class SystemCommandDefinition:
    name: str
    description: str
    effect: str
    argument_mode: str = "none"
    default_arguments: str = ""
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
            "default_arguments": self.default_arguments,
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
        argument_mode="optional",
        default_arguments="保留后续任务所需的关键上下文",
        usage="/compact [压缩方向]",
    ),
    SystemCommandDefinition(
        name="clear",
        description="清空整个模型上下文，完整记录仍会保留",
        effect="clear_context",
    ),
    SystemCommandDefinition(
        name="schedule",
        description="创建、查看或管理工作区独立定时任务",
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
        and settings.tool_swarm_enabled
        and not settings.agent_subagent_kill_switch
        and settings.agent_subagent_rollout_cohort in EXECUTABLE_SUBAGENT_COHORTS
    )
    if settings is None:
        subagent_unavailable_reason = "无法读取 Swarm / 子 Agent 工具状态。"
    elif not settings.tool_swarm_enabled:
        subagent_unavailable_reason = "Swarm / 子 Agent 工具已由用户关闭。"
    elif settings.agent_subagent_kill_switch:
        subagent_unavailable_reason = "子 Agent 已被紧急停止开关禁用。"
    elif settings.agent_subagent_rollout_cohort not in EXECUTABLE_SUBAGENT_COHORTS:
        subagent_unavailable_reason = "当前发布批次不允许执行子 Agent。"
    else:
        subagent_unavailable_reason = None
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
            unavailable_reason=subagent_unavailable_reason,
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
) -> tuple[str, dict[str, object], dict[str, object]]:
    run_count = len(await manager.list_runs(task.id))
    definition = next((item for item in SYSTEM_COMMANDS if item.name == command), None)
    if definition is None:
        raise ResourceError("SYSTEM_COMMAND_NOT_FOUND", "找不到这个快捷操作。")
    normalized_arguments = arguments.strip()
    message, details, normalized_arguments = await _execute_command(
        manager=manager,
        task=task,
        definition=definition,
        command=command,
        arguments=normalized_arguments,
        session=session,
        settings=settings,
    )
    invocation = _command_invocation(command, normalized_arguments)
    raw_state = task.context_state if isinstance(task.context_state, dict) else {}
    history = [item for item in raw_state.get("command_history", []) if isinstance(item, dict)]
    command_message = {
        "id": f"command-{uuid4()}",
        "command": f"/{command}",
        "content": invocation,
        "arguments": normalized_arguments,
        "after_run_count": run_count,
        "created_at": utc_now().isoformat(),
    }
    task.context_state = {**raw_state, "command_history": [*history, command_message][-200:]}
    task.updated_at = utc_now()
    await manager.session.commit()
    return message, details, command_message


async def _execute_command(
    *, manager, task, definition, command, arguments, session, settings
) -> tuple[str, dict[str, object], str]:
    if definition.argument_mode == "required":
        return await _execute_parameterized_command(
            task, definition, command, arguments, session, settings
        )
    if definition.argument_mode == "none" and arguments:
        raise ValidationError(
            "SYSTEM_COMMAND_USAGE_INVALID",
            f"/{command} 不接受参数。",
            {"usage": definition.usage, "command": f"/{command}"},
        )
    if command == "compact":
        direction = arguments or definition.default_arguments
        details = await manager.compact(task, direction=direction)
        details["direction"] = direction
        message = _compaction_message(details)
        return message, details, direction
    return "已清空模型上下文；后续请求将从零开始，完整记录仍保留。", await manager.clear(task), arguments


async def _execute_parameterized_command(
    task, definition, command, arguments, session, settings
) -> tuple[str, dict[str, object], str]:
    if not arguments:
        raise ValidationError(
            "SYSTEM_COMMAND_ARGUMENTS_REQUIRED",
            f"此命令需要参数。用法：{definition.usage}",
            {"usage": definition.usage, "command": f"/{command}"},
        )
    if session is None or settings is None:
        raise RuntimeError("Parameterized command dependencies are unavailable")
    automation = AutomationCommandService(session, settings)
    if command == "schedule":
        message, details = await automation.execute_schedule(task, arguments)
    else:
        message, details = await automation.execute_heartbeat(task, arguments)
    return message, details, arguments


def _compaction_message(details: dict[str, object]) -> str:
    if details.get("status") == "failed":
        return f"未能完成对话整理（{details['failure_code']}）；原上下文保持不变。"
    return "已按指定方向整理较早的对话，完整记录仍保留。"


def _command_invocation(command: str, arguments: str) -> str:
    return f"/{command}" + (f" {arguments}" if arguments else "")
