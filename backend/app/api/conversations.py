import logging

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.artifacts import LocalArtifactStore
from app.core.config import Settings, get_settings
from app.core.errors import ResourceError, StateError, ValidationError
from app.db.session import get_session
from app.repositories.conversations import (
    ConversationRepository,
    conversation_summary,
    conversation_view,
)
from app.schemas.conversations import (
    ConversationShareSummary,
    ConversationShareView,
    ConversationSummary,
    ConversationUpdateRequest,
    ConversationView,
    SharedConversation,
)

router = APIRouter(prefix="/api", tags=["conversations"])
logger = logging.getLogger("astra.conversations")


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


@router.get("/conversations/{conversation_id}", response_model=ConversationView)
async def get_conversation(conversation_id: str, session: AsyncSession = Depends(get_session)):
    task = await require_conversation(ConversationRepository(session), conversation_id, detailed=True)
    return conversation_view(task)


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
    return conversation_summary(await repo.update(task, title=title, pinned=payload.pinned))


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: str, session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
):
    repo = ConversationRepository(session)
    task = await require_conversation(repo, conversation_id)
    try:
        keys = await repo.delete(task)
    except RuntimeError as exc:
        raise StateError("CONVERSATION_ACTIVE", "对话仍在执行，请等待结束或先取消运行。") from exc
    store = LocalArtifactStore(settings.artifact_store_path)
    for key in keys:
        try:
            store.delete(key)
        except Exception:
            logger.warning("conversation.artifact_cleanup_failed key=%s", key, exc_info=True)
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
