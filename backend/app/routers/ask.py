import logging
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from app.auth import get_current_user
from app.db import get_supabase
from app.models import AskRequest
from app.services import gemini, qa

log = logging.getLogger(__name__)

router = APIRouter(tags=["ask"])


def _load_history(conversation_id: str) -> list[tuple[str, str]]:
    res = (
        get_supabase()
        .table("messages")
        .select("role,content")
        .eq("conversation_id", conversation_id)
        .order("created_at", desc=True)
        .limit(qa.HISTORY_MESSAGES)
        .execute()
    )
    return [(m["role"], m["content"] or "") for m in reversed(res.data)]


def _save_exchange(conversation_id: str, question: str, answer: str, citations: list[dict]) -> str:
    """Persist the question then the answer (separate inserts so created_at orders them)."""
    messages = get_supabase().table("messages")
    messages.insert({"conversation_id": conversation_id, "role": "user", "content": question}).execute()
    res = messages.insert(
        {"conversation_id": conversation_id, "role": "assistant", "content": answer, "citations": citations}
    ).execute()
    return res.data[0]["id"]


@router.post("/ask")
def ask(body: AskRequest, user_id: str = Depends(get_current_user)) -> StreamingResponse:
    """Answer a question from the user's saved sources, streamed as Server-Sent Events.

    Events: `meta` {conversation_id} -> `delta` {text}* -> `done` {message_id, citations}
    (or `error` {message}). Both messages are saved only after the answer completes.
    """
    db = get_supabase()
    question = body.question.strip()
    if not question:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Question can't be empty")

    # Validate / create the conversation before streaming so failures are real HTTP errors.
    history: list[tuple[str, str]] = []
    if body.conversation_id:
        conversation_id = str(body.conversation_id)
        owned = (
            db.table("conversations")
            .select("id")
            .eq("id", conversation_id)
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if not owned.data:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
        history = _load_history(conversation_id)
    else:
        conversation_id = db.table("conversations").insert({"user_id": user_id}).execute().data[0]["id"]

    def events() -> Iterator[str]:
        yield qa.sse("meta", {"conversation_id": conversation_id})
        try:
            chunks = qa.retrieve(user_id, question)
            if not chunks:
                answer = qa.NO_SOURCES_ANSWER
                yield qa.sse("delta", {"text": answer})
            else:
                parts: list[str] = []
                prompt = qa.build_prompt(chunks, question)
                for piece in gemini.stream_answer(qa.SYSTEM_PROMPT, history, prompt):
                    parts.append(piece)
                    yield qa.sse("delta", {"text": piece})
                answer = "".join(parts)
                if not answer.strip():
                    raise gemini.GeminiError("Gemini returned an empty answer. Please try again.")

            citations = qa.build_citations(answer, chunks) if chunks else []
            message_id = _save_exchange(conversation_id, question, answer, citations)
            yield qa.sse("done", {"message_id": message_id, "citations": citations})
        except gemini.GeminiError as e:
            yield qa.sse("error", {"message": str(e)})
        except Exception:  # noqa: BLE001 - never break the stream without telling the client
            log.exception("Unexpected error answering a question")
            yield qa.sse("error", {"message": "Something went wrong while answering. Please try again."})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
