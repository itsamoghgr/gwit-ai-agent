"""
routers/kb.py — Thin HTTP layer for KB article endpoints.
All logic delegated to services/kb_service.py.
"""
from typing import Optional
from fastapi import APIRouter, Query
from services import kb_service

router = APIRouter(tags=["kb"])


@router.get("/kb-articles/{run_id}")
def get_kb_articles(run_id: str):
    """Generated KB articles with summary stats for a pipeline run."""
    return kb_service.fetch_kb_articles(run_id)


@router.get("/existing-kb")
def get_existing_kb(
    run_id: Optional[str] = Query(default=None, description="Enrich with utilization from this run"),
):
    """All existing KB articles, optionally enriched with utilization status."""
    return kb_service.fetch_existing_kb(run_id)
