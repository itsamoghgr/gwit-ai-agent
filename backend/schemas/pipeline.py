"""schemas/pipeline.py — DTOs for the Run Pipeline endpoints."""
from typing import Any, List, Literal, Optional
from pydantic import BaseModel


PhaseRunStatus = Literal["pending", "running", "complete", "failed"]
JobRunStatus   = Literal["queued", "running", "complete", "partial_failure", "failed"]


class RunPipelineRequest(BaseModel):
    phases: Optional[List[str]] = None
    run_id: Optional[str] = None


class RunPipelineResponse(BaseModel):
    job_id: str
    run_id: str


class PhaseInfo(BaseModel):
    phase: str
    description: str


class PhaseStatusOut(BaseModel):
    phase: str
    status: PhaseRunStatus
    started_at: Optional[str]   = None
    finished_at: Optional[str]  = None
    duration_s: Optional[float] = None
    error: Optional[str]        = None
    stats: Optional[dict]       = None


class JobStatusOut(BaseModel):
    job_id: str
    run_id: str
    status: JobRunStatus
    phases: List[PhaseStatusOut]
    started_at: Optional[str]  = None
    finished_at: Optional[str] = None
    log_tail: List[str]        = []


# SSE event DTOs (documentation only — events are emitted as raw `data: {...}`
# lines from services/pipeline_service.py, not serialized by FastAPI).
class ProgressEvent(BaseModel):
    type: Literal["progress"] = "progress"
    phase: str
    label: str
    sub_phase: Optional[str] = None
    current: Optional[int]   = None
    total: Optional[int]     = None
    detail: Optional[str]    = None
    ts: Optional[str]        = None
