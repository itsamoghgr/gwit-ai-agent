"""routers/runs.py — Pipeline run listing for the sidebar selector."""
from fastapi import APIRouter, HTTPException
from schemas.kb import RunOut
from services.kb_service import fetch_runs, delete_run
from services import pipeline_service

router = APIRouter(tags=["runs"])


@router.get("/runs", response_model=list[RunOut])
def list_runs(include_hidden: bool = False, require_clusters: bool = True):
    """Return pipeline runs ordered by recency.

    `include_hidden=true` also returns runs in the HIDDEN_RUN_IDS list,
    each marked with `hidden: true`.
    `require_clusters=false` returns runs that haven't produced cluster rows —
    useful for the Manage modal so stale / aborted runs can be deleted.
    """
    return fetch_runs(include_hidden=include_hidden, require_clusters=require_clusters)


@router.delete("/runs/{run_id}")
def remove_run(run_id: str):
    """Delete a pipeline run and all of its per-run rows across child tables.

    Refuses to delete if there is an active in-memory job for this run_id,
    since deleting mid-flight corrupts tables the phase is still writing to.
    """
    if pipeline_service.find_active_job_by_run_id(run_id) is not None:
        raise HTTPException(
            status_code=409,
            detail="run is still active — stop the pipeline before deleting",
        )
    deleted = delete_run(run_id)
    if deleted == 0:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return {"run_id": run_id, "deleted": True}
