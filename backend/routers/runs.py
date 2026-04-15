"""routers/runs.py — Pipeline run listing for the sidebar selector."""
from fastapi import APIRouter
from schemas.kb import RunOut
from services.kb_service import fetch_runs

router = APIRouter(tags=["runs"])


@router.get("/runs", response_model=list[RunOut])
def list_runs():
    """Return all visible pipeline runs ordered by recency."""
    return fetch_runs()
