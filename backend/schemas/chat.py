"""schemas/chat.py — Pydantic request / response models for AI Chat."""
from typing import Literal, Optional
from pydantic import BaseModel, Field


class ChatHistoryMessage(BaseModel):
    role:    Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    run_id:  str
    message: str
    top_k:   int = Field(default=4, ge=1, le=10)
    history: list[ChatHistoryMessage] = Field(default_factory=list)


class KBSourceOut(BaseModel):
    title:        str
    category:     str
    snippet:      str
    is_generated: bool
    similarity:   float
