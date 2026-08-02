from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.repositories.tool_settings import ToolSettingsRepository, default_tool_states
from app.schemas.agent import EXECUTABLE_SUBAGENT_COHORTS
from app.tools.registry import sandbox_available

router = APIRouter(prefix="/api/tools", tags=["tools"])


class ToolToggle(BaseModel):
    name: str
    label: str
    description: str
    enabled: bool
    available: bool
    unavailable_reason: str | None = None


class ToolSettingsResponse(BaseModel):
    tools: list[ToolToggle]


class ToolSettingsUpdate(BaseModel):
    web_search: bool | None = None
    web_fetch: bool | None = None
    chart_render: bool | None = None
    bash_execute: bool | None = None
    swarm: bool | None = None


def _tool_settings(settings: Settings, states: dict[str, bool]) -> ToolSettingsResponse:
    sandbox_ready = settings.sandbox_enabled and sandbox_available(settings)
    unavailable_reason = None
    if not settings.sandbox_enabled:
        unavailable_reason = "需要先启用安全运行环境。"
    elif not sandbox_ready:
        unavailable_reason = "安全运行环境当前不可用。"
    swarm_available = bool(
        not settings.agent_subagent_kill_switch
        and settings.agent_subagent_rollout_cohort in EXECUTABLE_SUBAGENT_COHORTS
    )
    if settings.agent_subagent_kill_switch:
        swarm_unavailable_reason = "子 Agent 已被紧急停止开关禁用。"
    elif settings.agent_subagent_rollout_cohort not in EXECUTABLE_SUBAGENT_COHORTS:
        swarm_unavailable_reason = "当前发布批次不允许执行子 Agent。"
    else:
        swarm_unavailable_reason = None
    return ToolSettingsResponse(
        tools=[
            ToolToggle(
                name="web_search",
                label="Web Search",
                description="搜索公开网页",
                enabled=states["web_search"],
                available=sandbox_ready,
                unavailable_reason=unavailable_reason,
            ),
            ToolToggle(
                name="web_fetch",
                label="Web Fetch",
                description="提取页面主要内容",
                enabled=states["web_fetch"],
                available=sandbox_ready,
                unavailable_reason=unavailable_reason,
            ),
            ToolToggle(
                name="chart_render",
                label="Chart Render",
                description="生成图表",
                enabled=states["chart_render"],
                available=sandbox_ready,
                unavailable_reason=unavailable_reason,
            ),
            ToolToggle(
                name="bash_execute",
                label="Bash Execute",
                description="在受保护的临时环境中执行命令",
                enabled=states["bash_execute"],
                available=sandbox_ready,
                unavailable_reason=unavailable_reason,
            ),
            ToolToggle(
                name="swarm",
                label="Swarm / 子 Agent",
                description="并发创建受治理的子 Agent 并自动汇合结果",
                enabled=states["swarm"],
                available=swarm_available,
                unavailable_reason=swarm_unavailable_reason,
            ),
        ]
    )


@router.get("", response_model=ToolSettingsResponse)
async def get_tool_settings(
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> ToolSettingsResponse:
    states = await ToolSettingsRepository(session).get_or_create(default_tool_states(settings))
    await session.commit()
    return _tool_settings(settings, states)


@router.put("", response_model=ToolSettingsResponse)
async def update_tool_settings(
    update: ToolSettingsUpdate,
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> ToolSettingsResponse:
    repository = ToolSettingsRepository(session)
    await repository.set_all(
        update.model_dump(exclude_none=True), default_tool_states(settings)
    )
    states = await repository.get_or_create(default_tool_states(settings))
    await session.commit()
    return _tool_settings(settings, states)
