"""
routers/pipeline.py — HTTP surface for the pipeline orchestrator.

Endpoints:
  GET  /api/pipeline/phases          → phase catalog
  POST /api/pipeline/run             → start a job (returns immediately)
  GET  /api/pipeline/stream/{job_id} → live SSE stream
  GET  /api/pipeline/jobs/{job_id}   → polling fallback
  GET  /api/pipeline/jobs            → recent jobs
"""
import asyncio
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from pipeline.registry import PHASES, DEFAULT_ORDER
from schemas.pipeline import (
    PhaseInfo, RunPipelineRequest, RunPipelineResponse, JobStatusOut,
)
from services import pipeline_service

router = APIRouter(tags=["pipeline"])


@router.get("/pipeline/phases", response_model=list[PhaseInfo])
def get_phases():
    """Return the ordered phase catalog so the frontend doesn't hardcode it."""
    return [PhaseInfo(phase=p, description=PHASES[p][0]) for p in DEFAULT_ORDER]


@router.post("/pipeline/run", response_model=RunPipelineResponse)
async def start_run(req: RunPipelineRequest):
    """
    Create and launch a pipeline job. Returns { job_id, run_id } immediately;
    the caller subscribes to /pipeline/stream/{job_id} for live progress.
    """
    try:
        job = pipeline_service.create_job(req.phases, req.run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    asyncio.create_task(pipeline_service.execute_job(job.job_id))
    return RunPipelineResponse(job_id=job.job_id, run_id=job.run_id)


@router.get("/pipeline/stream/{job_id}")
async def stream_run(job_id: str):
    """SSE stream of phase_start / phase_complete / log / done events."""
    return StreamingResponse(
        pipeline_service.stream_job(job_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/pipeline/jobs/{job_id}", response_model=JobStatusOut)
def get_run(job_id: str):
    """Polling fallback for clients that can't hold an SSE connection."""
    job = pipeline_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job.to_status_dict()


@router.get("/pipeline/jobs", response_model=list[JobStatusOut])
def list_runs():
    """Recent jobs (most recent first)."""
    return pipeline_service.list_jobs()


@router.post("/pipeline/jobs/by-run/{run_id}/cancel")
def cancel_by_run_id(run_id: str):
    """Cooperatively cancel the in-flight job for a run_id (current phase finishes).

    If no in-memory job exists but the DB row still says 'running' (zombie from
    a prior restart), force-mark the row as 'failed' so the user can clean up.
    """
    job = pipeline_service.find_active_job_by_run_id(run_id)
    if job is not None:
        if not pipeline_service.cancel_job(job.job_id):
            raise HTTPException(status_code=409, detail="job is not cancellable")
        return {"job_id": job.job_id, "run_id": run_id, "cancel_requested": True}

    if pipeline_service.force_mark_run_failed(run_id):
        return {"job_id": None, "run_id": run_id, "forced_failed": True}

    raise HTTPException(status_code=404, detail=f"no active job for run {run_id}")
