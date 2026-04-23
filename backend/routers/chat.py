"""
routers/chat.py — AI Chat endpoint (Server-Sent Events streaming).
All RAG logic delegated to services/chat_service.py.
"""
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from schemas.chat import ChatRequest
from services import chat_service

router = APIRouter(tags=["chat"])


@router.get("/chat/suggested-questions")
def get_suggested_questions():
    """Return the list of suggested starter questions shown on the chat welcome screen."""
    return {"questions": chat_service.SUGGESTED_QUESTIONS}


@router.post("/chat")
def chat(req: ChatRequest):
    """
    Full RAG pipeline as a Server-Sent Events stream.

    SSE event format:
      data: {"type": "sources", "sources": [...]}   ← first event
      data: {"type": "token",   "text": "..."}      ← one per GPT token
      data: [DONE]                                   ← terminal event
    """
    return StreamingResponse(
        chat_service.stream_reply(
            message=req.message,
            run_id=req.run_id,
            history=[h.model_dump() for h in req.history],
            top_k=req.top_k,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
