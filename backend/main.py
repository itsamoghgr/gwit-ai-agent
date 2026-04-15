"""
main.py — FastAPI application factory.
Run: cd app/backend && uvicorn main:app --reload --port 8000
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import runs, clusters, kb, chat

app = FastAPI(
    title="GW IT Dashboard API",
    description="Read-only data API + AI Chat for the GW IT Support Dashboard",
    version="1.0.0",
)

# Allow the Next.js dev server to call the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register all routers
app.include_router(runs.router,     prefix="/api")
app.include_router(clusters.router, prefix="/api")
app.include_router(kb.router,       prefix="/api")
app.include_router(chat.router,     prefix="/api")


@app.get("/api/health", tags=["health"])
def health_check():
    """Liveness probe — confirms the API is running."""
    return {"status": "ok"}
