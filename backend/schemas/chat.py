"""schemas/chat.py — Pydantic request / response models for AI Chat."""
from typing import Optional
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    run_id:  str
    message: str
    top_k:   int = Field(default=4, ge=1, le=10)


class KBSourceOut(BaseModel):
    title:        str
    category:     str
    snippet:      str
    is_generated: bool
    similarity:   float
