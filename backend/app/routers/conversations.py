from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import get_current_user
from app.db import get_supabase
from app.models import ConversationOut, MessageOut

router = APIRouter(prefix="/conversations", tags=["conversations"])

MESSAGE_LIMIT = 500


@router.post("", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
def create_conversation(user_id: str = Depends(get_current_user)) -> dict:
    res = get_supabase().table("conversations").insert({"user_id": user_id}).execute()
    return res.data[0]


@router.get("/{conversation_id}/messages", response_model=list[MessageOut])
def list_messages(conversation_id: UUID, user_id: str = Depends(get_current_user)) -> list[dict]:
    db = get_supabase()
    owned = (
        db.table("conversations")
        .select("id")
        .eq("id", str(conversation_id))
        .eq("user_id", user_id)  # ownership check before exposing any messages
        .limit(1)
        .execute()
    )
    if not owned.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    res = (
        db.table("messages")
        .select("id,role,content,citations,created_at")
        .eq("conversation_id", str(conversation_id))
        .order("created_at")
        .limit(MESSAGE_LIMIT)
        .execute()
    )
    return res.data
