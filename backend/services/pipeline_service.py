"""
services/pipeline_service.py — Pipeline job orchestrator.

Responsibilities:
- In-memory job registry (process-local; single-worker uvicorn assumption)
- run_id resolution (reuse latest run with prereq rows, or mint a fresh UUID)
- Async execute_job coroutine that runs phases sequentially via asyncio.to_thread
- SSE stream generator with late-attach snapshot support
- Persistent status writes to pipeline_runs (mirrors legacy semantics)

Phase functions are imported via pipeline.registry and run as blocking code
inside asyncio.to_thread, so the event loop stays responsive.
"""
from __future__ import annotations

import asyncio
import json
import logging
import queue
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from typing import AsyncIterator, Dict, List, Optional, Tuple

from sqlalchemy import text

from core.database import get_engine
from core.pipeline_logger import (
    LogTail, make_logger, make_progress, _current_log_sink,
)
from pipeline.registry import PHASES, DEFAULT_ORDER, PREREQ_TABLE

_log = logging.getLogger("pipeline.service")

MAX_JOBS_RETAINED = 50


# ── Data classes ────────────────────────────────────────────────────────────

@dataclass
class PhaseState:
    phase: str
    status: str = "pending"            # pending | running | complete | failed
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_s: Optional[float] = None
    error: Optional[str] = None
    stats: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_s": self.duration_s,
            "error": self.error,
            "stats": self.stats,
        }


@dataclass
class Job:
    job_id: str
    run_id: str
    status: str = "queued"             # queued | running | complete | partial_failure | failed | cancelled
    phases: List[PhaseState] = field(default_factory=list)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    log_tail: LogTail = field(default_factory=LogTail)
    subscribers: List["queue.Queue[dict]"] = field(default_factory=list)
    done_event: asyncio.Event = field(default_factory=asyncio.Event)
    cancel_requested: bool = False

    def to_status_dict(self) -> dict:
        return {
            "job_id":      self.job_id,
            "run_id":      self.run_id,
            "status":      self.status,
            "phases":      [p.to_dict() for p in self.phases],
            "started_at":  self.started_at,
            "finished_at": self.finished_at,
            "log_tail":    self.log_tail.snapshot(),
        }


# ── Registry ────────────────────────────────────────────────────────────────

_JOBS: "OrderedDict[str, Job]" = OrderedDict()


def _register(job: Job) -> None:
    _JOBS[job.job_id] = job
    while len(_JOBS) > MAX_JOBS_RETAINED:
        _JOBS.popitem(last=False)


def get_job(job_id: str) -> Optional[Job]:
    return _JOBS.get(job_id)


def list_jobs() -> list[dict]:
    return [j.to_status_dict() for j in reversed(list(_JOBS.values()))]


def find_active_job_by_run_id(run_id: str) -> Optional[Job]:
    """Return the most recent non-terminal job for a given run_id, if any."""
    for job in reversed(list(_JOBS.values())):
        if job.run_id == run_id and job.status in ("queued", "running"):
            return job
    return None


def cancel_job(job_id: str) -> bool:
    """Request cooperative cancellation. Returns False if job is unknown or already terminal."""
    job = _JOBS.get(job_id)
    if job is None:
        return False
    if job.status not in ("queued", "running"):
        return False
    job.cancel_requested = True
    return True


def sweep_orphaned_running_rows() -> int:
    """Mark DB rows stuck at status='running' as 'failed' on app boot.

    The in-memory job registry doesn't survive a restart, so any pipeline_runs
    row still showing 'running' is guaranteed to be a zombie. Returns the
    number of rows updated.
    """
    try:
        engine = get_engine()
        with engine.begin() as conn:
            result = conn.execute(text(
                "UPDATE pipeline_runs "
                "SET status='failed', finished_at=:ts "
                "WHERE status IN ('running', 'queued')"
            ), {"ts": datetime.utcnow()})
            count = result.rowcount or 0
        if count:
            _log.info("pipeline: swept %d orphaned running row(s) to failed", count)
        return count
    except Exception:
        _log.exception("pipeline: orphan sweep failed")
        return 0


def force_mark_run_failed(run_id: str) -> bool:
    """Force a pipeline_runs row to status='failed'. Used to recover from zombies
    where the DB row says 'running' but no in-memory job exists (e.g., after a
    server restart). Returns True if a row was updated."""
    try:
        engine = get_engine()
        with engine.begin() as conn:
            result = conn.execute(text(
                "UPDATE pipeline_runs "
                "SET status='failed', finished_at=:ts "
                "WHERE run_id=:r AND status IN ('running', 'queued')"
            ), {"ts": datetime.utcnow(), "r": run_id})
            return (result.rowcount or 0) > 0
    except Exception:
        _log.exception("pipeline: force_mark_run_failed failed")
        return False


# ── SSE fan-out ─────────────────────────────────────────────────────────────

def _publish(job: Job, event: dict) -> None:
    """
    Push one event to every subscriber of this job.

    Phase code runs inside asyncio.to_thread, so this is called from a worker
    thread. We use stdlib queue.Queue (thread-safe, loop-agnostic) so worker
    and event-loop threads can both put/get without hopping onto a specific
    asyncio loop — that hop was the source of dropped SSE events.
    """
    for q in list(job.subscribers):
        try:
            q.put_nowait(event)
        except queue.Full:
            _log.warning("pipeline subscriber queue full; dropping event")
        except Exception:
            _log.exception("pipeline _publish failed")


# ── run_id resolution ───────────────────────────────────────────────────────

def _resolve_run_id(engine, phases: List[str], explicit: Optional[str]) -> Tuple[str, bool]:
    """
    Returns (run_id, reused). Mirrors legacy run_pipeline.py behavior:
    - explicit → reuse that run
    - single phase with a prereq → reuse the latest run whose prereq table has rows
    - otherwise → mint a fresh UUID
    """
    if explicit:
        return explicit, True

    if len(phases) == 1 and phases[0] in PREREQ_TABLE:
        prereq = PREREQ_TABLE[phases[0]]
        sql = text(f"""
            SELECT pr.run_id
            FROM   pipeline_runs pr
            INNER JOIN (SELECT DISTINCT run_id FROM {prereq}) t USING (run_id)
            ORDER  BY pr.started_at DESC
            LIMIT  1
        """)
        try:
            with engine.connect() as conn:
                row = conn.execute(sql).fetchone()
            if row:
                return str(row[0]), True
        except Exception:
            # pipeline_runs / prereq table may not exist yet on a fresh DB
            pass

    return str(uuid.uuid4()), False


def _upsert_run_row(engine, run_id: str, phases: List[str], reused: bool) -> None:
    """Insert or update the pipeline_runs row for this job."""
    try:
        with engine.begin() as conn:
            if reused:
                conn.execute(
                    text("UPDATE pipeline_runs SET status='running' WHERE run_id=:r"),
                    {"r": run_id},
                )
            else:
                conn.execute(
                    text("""
                        INSERT INTO pipeline_runs (run_id, started_at, status, config_snapshot)
                        VALUES (:r, :ts, 'running', CAST(:cfg AS jsonb))
                        ON CONFLICT (run_id) DO UPDATE SET status='running'
                    """),
                    {
                        "r":   run_id,
                        "ts":  datetime.utcnow(),
                        "cfg": json.dumps({"phases": phases}),
                    },
                )
    except Exception:
        # pipeline_runs table may not exist yet — non-fatal for M0 smoke testing.
        pass


def _finalize_run_row(engine, run_id: str, status: str) -> None:
    try:
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE pipeline_runs SET status=:s, finished_at=:ts WHERE run_id=:r"),
                {"s": status, "ts": datetime.utcnow(), "r": run_id},
            )
    except Exception:
        pass


# ── Public API ──────────────────────────────────────────────────────────────

def create_job(phases: Optional[List[str]], explicit_run_id: Optional[str]) -> Job:
    phases = phases or list(DEFAULT_ORDER)
    unknown = [p for p in phases if p not in PHASES]
    if unknown:
        raise ValueError(f"Unknown phase(s): {unknown}. Valid: {list(PHASES)}")

    engine = get_engine()
    run_id, reused = _resolve_run_id(engine, phases, explicit_run_id)
    _upsert_run_row(engine, run_id, phases, reused)

    job = Job(
        job_id=str(uuid.uuid4()),
        run_id=run_id,
        phases=[PhaseState(phase=p) for p in phases],
    )
    _register(job)
    return job


async def execute_job(job_id: str) -> None:
    """Run the job's phases sequentially. Never raises — errors recorded per-phase."""
    job = _JOBS.get(job_id)
    if job is None:
        return

    job.status = "running"
    job.started_at = datetime.utcnow().isoformat()
    _publish(job, {"type": "job_start", "job_id": job.job_id, "run_id": job.run_id})

    engine = get_engine()
    failed: List[str] = []

    log = make_logger(
        on_line=lambda line: _publish(job, {"type": "log", "line": line}),
        tail=job.log_tail,
    )
    progress = make_progress(
        publish_event=lambda ev: _publish(job, ev),
        tail=job.log_tail,
    )

    for phase_state in job.phases:
        if job.cancel_requested:
            # Skip remaining phases; mark them cancelled so the UI shows why.
            phase_state.status = "cancelled"
            phase_state.finished_at = datetime.utcnow().isoformat()
            _publish(job, {
                "type": "phase_failed",
                "phase": phase_state.phase,
                "duration_s": 0,
                "error": "cancelled by user",
            })
            continue

        phase_state.status = "running"
        phase_state.started_at = datetime.utcnow().isoformat()
        _publish(job, {"type": "phase_start", "phase": phase_state.phase})

        t0 = time.monotonic()
        try:
            phase_fn = PHASES[phase_state.phase][1]
            # Ported phases use the legacy signature run(engine, run_id) and read
            # the SSE log sink from a ContextVar. asyncio.to_thread propagates
            # the current context (incl. the ContextVar value we set here) into
            # the worker thread, so `get_phase_logger(...)` inside the phase
            # emits to this job's SSE stream.
            token = _current_log_sink.set(log)
            try:
                progress(phase_state.phase, f"Phase {phase_state.phase} starting")
                stats = await asyncio.to_thread(phase_fn, engine, job.run_id)
                progress(phase_state.phase, f"Phase {phase_state.phase} complete")
            finally:
                _current_log_sink.reset(token)
            phase_state.stats = stats if isinstance(stats, dict) else {"result": stats}
            phase_state.status = "complete"
        except Exception as exc:
            phase_state.status = "failed"
            phase_state.error = f"{type(exc).__name__}: {exc}"
            failed.append(phase_state.phase)
            log(f"phase {phase_state.phase} FAILED: {phase_state.error}")
        finally:
            phase_state.duration_s = round(time.monotonic() - t0, 2)
            phase_state.finished_at = datetime.utcnow().isoformat()

        if phase_state.status == "complete":
            _publish(job, {
                "type": "phase_complete",
                "phase": phase_state.phase,
                "duration_s": phase_state.duration_s,
                "stats": phase_state.stats,
            })
        else:
            _publish(job, {
                "type": "phase_failed",
                "phase": phase_state.phase,
                "duration_s": phase_state.duration_s,
                "error": phase_state.error,
            })

    if job.cancel_requested:
        job.status = "cancelled"
    elif failed:
        job.status = "partial_failure"
    else:
        job.status = "complete"
    job.finished_at = datetime.utcnow().isoformat()
    _finalize_run_row(engine, job.run_id, job.status)

    _publish(job, {
        "type": "done",
        "status": job.status,
        "failed_phases": failed,
    })
    job.done_event.set()


async def stream_job(job_id: str) -> AsyncIterator[str]:
    """
    SSE generator for a job. Late subscribers get a single `snapshot` event
    containing the current JobStatus, then tail live events.
    """
    job = _JOBS.get(job_id)
    if job is None:
        yield f"data: {json.dumps({'type': 'error', 'message': 'job not found'})}\n\n"
        yield "data: [DONE]\n\n"
        return

    sub: "queue.Queue[dict]" = queue.Queue(maxsize=1024)
    job.subscribers.append(sub)

    # Replay current state so a late attach still sees finished phases.
    yield f"data: {json.dumps({'type': 'snapshot', 'job': job.to_status_dict()})}\n\n"

    if job.done_event.is_set():
        yield "data: [DONE]\n\n"
        job.subscribers.remove(sub)
        return

    try:
        while True:
            try:
                # Blocking get offloaded to a thread so the event loop stays
                # free. Using stdlib queue.Queue keeps put/get loop-agnostic.
                event = await asyncio.to_thread(sub.get, True, 15.0)
            except queue.Empty:
                # Heartbeat keeps proxies from closing idle SSE connections.
                yield ": heartbeat\n\n"
                if job.done_event.is_set():
                    break
                continue

            yield f"data: {json.dumps(event)}\n\n"
            if event.get("type") == "done":
                break
    finally:
        if sub in job.subscribers:
            job.subscribers.remove(sub)
        yield "data: [DONE]\n\n"
