from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.agent import RunView


class ConversationSummary(BaseModel):
    id: str
    title: str
    title_source: str = "auto"
    pinned_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    last_run_status: str | None = None
    last_message_preview: str = ""
    has_active_share: bool = False


class ConversationView(ConversationSummary):
    runs: list[RunView] = Field(default_factory=list)


class ConversationUpdateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=240)
    pinned: bool | None = None


class PublicMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class SharedConversation(BaseModel):
    title: str
    messages: list[PublicMessage]
    shared_at: datetime
    updated_at: datetime


class ConversationShareView(BaseModel):
    url: str
    created_at: datetime
    updated_at: datetime

