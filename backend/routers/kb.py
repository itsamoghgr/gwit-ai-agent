"""
routers/kb.py — Thin HTTP layer for KB article endpoints.
All logic delegated to services/kb_service.py.
"""
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from schemas.kb import KBArticleUpdate
from services import kb_service

router = APIRouter(tags=["kb"])


@router.get("/kb-articles/{run_id}")
def get_kb_articles(run_id: str):
    """Generated KB articles with summary stats for a pipeline run."""
    return kb_service.fetch_kb_articles(run_id)


@router.patch("/kb-articles/{run_id}/{cluster_id}")
def patch_kb_article(run_id: str, cluster_id: int, patch: KBArticleUpdate):
    """Update editable fields of a generated KB article."""
    ok = kb_service.update_kb_article(run_id, cluster_id, patch)
    if not ok:
        raise HTTPException(status_code=404, detail="Article not found or no fields to update")
    return {"ok": True}


@router.post("/kb-articles/{run_id}/reindex")
def reindex_kb_articles(run_id: str):
    """Rebuild kb_search_index entries (source='generated') for this run so
    the AI Chat retrieval can actually find every generated article. Existing
    KB rows and the generated_kb_articles table itself are left untouched."""
    return kb_service.reindex_generated_articles(run_id)


@router.get("/existing-kb")
def get_existing_kb(
    run_id: Optional[str] = Query(default=None, description="Enrich with utilization from this run"),
):
    """All existing KB articles, optionally enriched with utilization status."""
    return kb_service.fetch_existing_kb(run_id)
