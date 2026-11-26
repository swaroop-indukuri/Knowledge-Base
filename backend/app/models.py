from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, Field


class HealthResponse(BaseModel):
    status: str


class MeResponse(BaseModel):
    user_id: str


class SourceCreate(BaseModel):
    url: AnyHttpUrl = Field(max_length=2048)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: UUID | None = None


class ConversationOut(BaseModel):
    id: UUID
    created_at: datetime


class MessageOut(BaseModel):
    id: UUID
    role: Literal["user", "assistant"]
    content: str | None = None
    citations: list[dict[str, Any]] = []
    created_at: datetime


class FileUrlOut(BaseModel):
    url: str
    expires_in: int


class SourceOut(BaseModel):
    id: UUID
    type: Literal["url", "pdf", "youtube"]
    url: str | None = None
    title: str | None = None
    summary: str | None = None
    category: str | None = None
    status: Literal["processing", "ready", "failed"]
    error: str | None = None
    created_at: datetime
