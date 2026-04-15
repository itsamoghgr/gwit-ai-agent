"""
services/chat_service.py
RAG pipeline: embed → pgvector cosine search → GPT-4.1 stream.
All Azure OpenAI and pgvector logic lives here.
"""
import os
from functools import lru_cache
from typing import Dict, Generator, List

import psycopg2
from openai import AzureOpenAI
from pgvector.psycopg2 import register_vector

from core.config import get_settings
from core.database import get_engine
from schemas.chat import KBSourceOut

# Similarity threshold: below this, sources are not shown to the user
SOURCE_SIM_THRESHOLD = 0.87

SYSTEM_PROMPT = """\
You are Archie, the GW IT Help Desk AI assistant for George Washington University.
You help GW faculty, staff, and students resolve IT issues in a friendly, conversational way.

Behaviour guidelines:
- Respond naturally — match the tone of the user's message.
- For technical questions, use the Knowledge Base context provided to give clear, accurate answers.
- When the KB context is relevant, structure your technical answers with numbered steps.
- If the KB context doesn't cover the question, say so honestly and refer the user to:
  GWU IT Support: 202-994-4948 | ithelp.gwu.edu
- You can ask clarifying questions when a problem is ambiguous.
- Never invent steps or information not in the context.
- You remember the conversation history — reference earlier messages naturally when relevant.

Knowledge Base Context (use this to answer technical questions):
{context}"""

SUGGESTED_QUESTIONS = [
    "How do I reset my GWU NetID password?",
    "I can't connect to GWU WiFi on my laptop",
    "How do I set up MFA for my GWU account?",
    "How can I access Microsoft Office as a student?",
    "My GWU email is not working — what should I do?",
]


@lru_cache(maxsize=1)
def _az_client() -> AzureOpenAI:
    s = get_settings()
    return AzureOpenAI(
        azure_endpoint=s.AZURE_OPENAI_ENDPOINT,
        api_key=s.AZURE_OPENAI_API_KEY,
        api_version=s.AZURE_OPENAI_API_VERSION,
    )


def _psycopg2_conn():
    """Open a raw psycopg2 connection (required for pgvector <=> operator)."""
    url = get_engine().url
    conn = psycopg2.connect(
        host=str(url.host),
        port=url.port or 5432,
        dbname=str(url.database),
        user=str(url.username),
        password=str(url.password),
    )
    register_vector(conn)
    return conn


def embed(text: str) -> List[float]:
    """Embed a text string using Azure OpenAI."""
    s = get_settings()
    return (
        _az_client()
        .embeddings.create(model=s.AZURE_OPENAI_EMBED_MODEL, input=text)
        .data[0]
        .embedding
    )


def vector_search(query_vec: List[float], run_id: str, top_k: int = 4) -> List[KBSourceOut]:
    """cosine similarity search against kb_search_index via pgvector."""
    conn = _psycopg2_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT title, category, content, is_generated,
                   1 - (embedding <=> %s::vector) AS similarity
            FROM   kb_search_index
            WHERE  run_id = %s AND embedding IS NOT NULL
            ORDER  BY embedding <=> %s::vector
            LIMIT  %s
        """, (query_vec, run_id, query_vec, top_k))
        rows = cur.fetchall()
        cur.close()
    finally:
        conn.close()

    return [
        KBSourceOut(
            title=row[0],
            category=row[1],
            snippet=(row[2] or "")[:400].strip(),
            is_generated=bool(row[3]),
            similarity=max(0.0, float(row[4])),
        )
        for row in rows
    ]


def build_context(sources: List[KBSourceOut]) -> str:
    """Format KB sources into a context block for the system prompt."""
    parts = []
    for i, src in enumerate(sources, 1):
        kind = "Generated KB" if src.is_generated else "Existing KB"
        parts.append(f"[{i}] {src.title} ({kind} | {src.category})\n{src.snippet}")
    return "\n\n".join(parts)


def stream_reply(
    message: str,
    run_id: str,
    history: List[Dict],
    top_k: int = 4,
) -> Generator[str, None, None]:
    """
    Full RAG pipeline yielding SSE-formatted chunks:
      - First chunk: JSON metadata (sources)
      - Subsequent chunks: text delta tokens
    """
    import json

    s = get_settings()

    # 1. Embed the user query
    q_vec = embed(message)

    # 2. Retrieve top-K KB chunks
    sources = vector_search(q_vec, run_id, top_k)

    # 3. Yield sources metadata as first SSE event
    relevant = [src for src in sources if src.similarity >= SOURCE_SIM_THRESHOLD]
    yield f"data: {json.dumps({'type': 'sources', 'sources': [s.model_dump() for s in relevant]})}\n\n"

    # 4. Build messages list (system + history + current user turn)
    context  = build_context(sources)
    messages = [{"role": "system", "content": SYSTEM_PROMPT.format(context=context)}]
    for h in history[-12:]:
        if h.get("role") in ("user", "assistant"):
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": message})

    # 5. Stream GPT-4.1 response
    stream = _az_client().chat.completions.create(
        model=s.AZURE_OPENAI_DEPLOYMENT,
        messages=messages,
        stream=True,
        temperature=0.4,
        max_tokens=700,
    )
    for chunk in stream:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            yield f"data: {json.dumps({'type': 'token', 'text': delta})}\n\n"

    yield "data: [DONE]\n\n"
