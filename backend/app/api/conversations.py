from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.context_commands import execute_system_command, list_system_commands
from app.conversation_context import ConversationContextManager
from app.conversation_lifecycle import ConversationLifecycleService
from app.core.config import Settings, get_settings
from app.core.errors import ResourceError, StateError, ValidationError
from app.db.session import get_session
from app.repositories.conversations import (
    ConversationRepository,
    conversation_summary,
    conversation_view,
)
from app.repositories.schedules import ScheduleRepository
from app.repositories.tool_settings import (
    ToolSettingsRepository,
    apply_tool_states,
    default_tool_states,
)
from app.schemas.conversations import (
    ContextWindowStatus,
    ConversationCreateRequest,
    ConversationShareSummary,
    ConversationShareView,
    ConversationSummary,
    ConversationUpdateRequest,
    ConversationView,
    SharedConversation,
    SlashCommandRequest,
    SlashCommandResult,
    SlashSystemCommand,
)

router = APIRouter(prefix="/api", tags=["conversations"])


async def require_conversation(repo: ConversationRepository, conversation_id: str, *, detailed: bool = False):
    task = await repo.get(conversation_id, detailed=detailed)
    if task is None:
        raise ResourceError("CONVERSATION_NOT_FOUND", "找不到指定对话。")
    return task


@router.get("/conversations", response_model=list[ConversationSummary])
async def list_conversations(
    limit: int = Query(default=100, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
):
    return [conversation_summary(item) for item in await ConversationRepository(session).list(limit)]


@router.post("/conversations", response_model=ConversationSummary, status_code=201)
async def create_conversation(
    payload: ConversationCreateRequest,
    session: AsyncSession = Depends(get_session),
):
    title = payload.title.strip()
    if not title:
        raise ValidationError("CONVERSATION_TITLE_REQUIRED", "对话标题不能为空。")
    task = await ConversationRepository(session).create(
        title=title,
        preferred_answer_mode=payload.preferred_answer_mode,
    )
    return conversation_summary(task)


@router.get("/conversations/{conversation_id}", response_model=ConversationView)
async def get_conversation(conversation_id: str, session: AsyncSession = Depends(get_session)):
    task = await require_conversation(ConversationRepository(session), conversation_id, detailed=True)
    return conversation_view(task)


@router.get("/system-commands", response_model=list[SlashSystemCommand])
async def get_system_commands(
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
):
    states = await ToolSettingsRepository(session).get_or_create(
        default_tool_states(settings)
    )
    await session.commit()
    return list_system_commands(apply_tool_states(settings, states))


@router.get(
    "/conversations/{conversation_id}/context",
    response_model=ContextWindowStatus,
)
async def get_conversation_context(
    conversation_id: str,
    provider: str = Query(min_length=1, max_length=80),
    model: str = Query(min_length=1, max_length=160),
    draft: str = Query(default="", max_length=4000),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    repo = ConversationRepository(session)
    task = await require_conversation(repo, conversation_id)
    return await ConversationContextManager(session, settings).status(
        task,
        provider=provider,
        model=model,
        draft=draft,
    )


@router.post(
    "/conversations/{conversation_id}/commands/{command}",
    response_model=SlashCommandResult,
)
async def run_conversation_command(
    conversation_id: str,
    command: str,
    payload: SlashCommandRequest | None = None,
    provider: str = Query(min_length=1, max_length=80),
    model: str = Query(min_length=1, max_length=160),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    repo = ConversationRepository(session)
    task = await require_conversation(repo, conversation_id)
    manager = ConversationContextManager(session, settings)
    message, details, user_message = await execute_system_command(
        manager,
        task,
        command,
        arguments=payload.arguments if payload else "",
        session=session,
        settings=settings,
    )
    return {
        "command": f"/{command}",
        "message": message,
        "context": await manager.status(
            task,
            provider=provider,
            model=model,
        ),
        "details": details,
        "user_message": user_message,
    }


@router.patch("/conversations/{conversation_id}", response_model=ConversationSummary)
async def update_conversation(
    conversation_id: str, payload: ConversationUpdateRequest,
    session: AsyncSession = Depends(get_session),
):
    repo = ConversationRepository(session)
    task = await require_conversation(repo, conversation_id)
    title = payload.title.strip() if payload.title is not None else None
    if payload.title is not None and not title:
        raise ValidationError("CONVERSATION_TITLE_REQUIRED", "对话标题不能为空。")
    return conversation_summary(
        await repo.update(
            task,
            title=title,
            pinned=payload.pinned,
            preferred_answer_mode=payload.preferred_answer_mode,
        )
    )


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: str, session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    repo = ConversationRepository(session)
    task = await require_conversation(repo, conversation_id)
    bound_jobs = await ScheduleRepository(session).list(target_task_id=conversation_id, limit=1)
    if bound_jobs:
        raise StateError(
            "CONVERSATION_HAS_AUTOMATIONS",
            "该对话仍绑定定时任务或 Heartbeat，请先更换结果对话或删除任务。",
        )
    try:
        await ConversationLifecycleService(settings).delete(repo, task)
    except RuntimeError as exc:
        raise StateError("CONVERSATION_ACTIVE", "对话仍在执行，请等待结束或先取消运行。") from exc
    return Response(status_code=204)


def share_view(share) -> dict:
    return {"url": f"/share/{share.token}", "created_at": share.created_at, "updated_at": share.updated_at}


@router.get("/conversation-shares", response_model=list[ConversationShareSummary])
async def list_active_shares(session: AsyncSession = Depends(get_session)):
    shares = await ConversationRepository(session).list_active_shares()
    return [
        {
            **share_view(share),
            "conversation_id": share.conversation_id,
            "title": share.conversation.title,
            "message_count": sum(1 for message in share.snapshot.get("messages", []) if message.get("role") != "process"),
        }
        for share in shares
    ]


@router.post("/conversations/{conversation_id}/share", response_model=ConversationShareView)
async def create_share(conversation_id: str, session: AsyncSession = Depends(get_session)):
    repo = ConversationRepository(session)
    task = await require_conversation(repo, conversation_id, detailed=True)
    share = await repo.create_or_get_share(task)
    return share_view(share)


@router.put("/conversations/{conversation_id}/share", response_model=ConversationShareView)
async def update_share(conversation_id: str, session: AsyncSession = Depends(get_session)):
    repo = ConversationRepository(session)
    task = await require_conversation(repo, conversation_id, detailed=True)
    share = await repo.create_or_get_share(task, refresh=True)
    return share_view(share)


@router.delete("/conversations/{conversation_id}/share", status_code=204)
async def revoke_share(conversation_id: str, session: AsyncSession = Depends(get_session)):
    repo = ConversationRepository(session)
    task = await require_conversation(repo, conversation_id)
    await repo.revoke_share(task)
    return Response(status_code=204)


@router.get("/shared-conversations/{token}", response_model=SharedConversation)
async def get_shared_conversation(token: str, session: AsyncSession = Depends(get_session)):
    share = await ConversationRepository(session).get_public_share(token)
    if share is None:
        raise ResourceError("SHARE_NOT_FOUND", "分享链接不存在或已失效。")
    return {**share.snapshot, "shared_at": share.created_at, "updated_at": share.updated_at}
