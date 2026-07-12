from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.repositories.tool_settings import ToolSettingsRepository, default_tool_states
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
    web_search: bool
    web_fetch: bool
    chart_render: bool


def _tool_settings(settings: Settings, states: dict[str, bool]) -> ToolSettingsResponse:
    chart_available = settings.sandbox_enabled and sandbox_available(settings)
    chart_reason = None
    if not settings.sandbox_enabled:
        chart_reason = "需要先启用 Docker 沙箱。"
    elif not chart_available:
        chart_reason = "Docker 当前不可用。"
    return ToolSettingsResponse(
        tools=[
            ToolToggle(name="web_search", label="Web Search", description="搜索公开网页并生成候选来源", enabled=states["web_search"], available=True),
            ToolToggle(name="web_fetch", label="Web Fetch", description="自适应提取页面主要内容", enabled=states["web_fetch"], available=True),
            ToolToggle(name="chart_render", label="Chart Render", description="在隔离的 Docker 运行时中生成图表", enabled=states["chart_render"], available=chart_available, unavailable_reason=chart_reason),
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
    states = await ToolSettingsRepository(session).set_all(
        update.model_dump(), default_tool_states(settings)
    )
    await session.commit()
    return _tool_settings(settings, states)
