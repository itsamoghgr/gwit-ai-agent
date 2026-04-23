"""
main.py — FastAPI application factory.
Run: cd app/backend && uvicorn main:app --reload --port 8000
"""
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import runs, clusters, kb, chat, pipeline


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load app/.env into os.environ BEFORE any phase module is imported,
    # so phases' module-level os.getenv("AZURE_OPENAI_API_KEY", "") resolves.
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)

    # Idempotent schema bootstrap: CREATE EXTENSION vector + create_all + migrations.
    from core.database import get_engine
    from pipeline.schema import init_pipeline_schema
    init_pipeline_schema(get_engine())

    # Zombie sweep: any pipeline_runs row still at 'running' after a restart
    # cannot be truly running (in-memory job registry is process-local).
    from services.pipeline_service import sweep_orphaned_running_rows
    sweep_orphaned_running_rows()

    yield


app = FastAPI(
    title="GW IT Dashboard API",
    description="Read-only data API + AI Chat for the GW IT Support Dashboard",
    version="1.0.0",
    lifespan=lifespan,
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
app.include_router(pipeline.router, prefix="/api")


@app.get("/api/health", tags=["health"])
def health_check():
    """Liveness probe — confirms the API is running."""
    return {"status": "ok"}
