"""
core/pipeline_logger.py — Callback-based logger + progress emitter for phases.

Each phase receives two callables:
  log(line: str)                             — free-form status line
  progress(phase, label, *, sub_phase=None,  — structured milestone event
           current=None, total=None, detail=None)

The orchestrator fans them into:
  (1) SSE events {"type": "log", ...} or {"type": "progress", ...}
  (2) the job's in-memory log_tail ring buffer (for polling fallback)
  (3) stdout (so developers running uvicorn see output too)

Ported pipeline phases use the stdlib `logging` module. `get_phase_logger(name)`
returns a logger whose output is forwarded to the current job's LogSink via a
ContextVar that the orchestrator sets before running each phase.
"""
import contextvars
import logging
from datetime import datetime
from typing import Any, Callable, Deque, Dict, Optional
from collections import deque

LogSink = Callable[[str], None]
ProgressSink = Callable[..., None]
EventSink = Callable[[Dict[str, Any]], None]

MAX_TAIL = 200

# Set by services/pipeline_service.execute_job before invoking each phase.
# asyncio.to_thread propagates the caller's context into the worker thread
# (Python 3.9+ via contextvars.copy_context), so phase code reads the right sink.
_current_log_sink: contextvars.ContextVar[Optional[LogSink]] = contextvars.ContextVar(
    "pipeline_log_sink", default=None,
)


class LogTail:
    """Ring buffer for the latest N log lines on a job."""

    def __init__(self, maxlen: int = MAX_TAIL):
        self._buf: Deque[str] = deque(maxlen=maxlen)

    def append(self, line: str) -> None:
        self._buf.append(line)

    def snapshot(self) -> list[str]:
        return list(self._buf)


def make_logger(on_line: LogSink, tail: LogTail, *, mirror_stdout: bool = True) -> LogSink:
    """
    Build the `log` callable phases receive.
    `on_line` pushes formatted text to the job's SSE queue.
    `tail` retains the last ~200 lines for polling consumers.
    """
    def log(line: str) -> None:
        stamp = datetime.utcnow().strftime("%H:%M:%S")
        formatted = f"[{stamp}] {line}"
        tail.append(formatted)
        try:
            on_line(formatted)
        except Exception:
            pass
        if mirror_stdout:
            print(formatted, flush=True)

    return log


def make_progress(
    publish_event: EventSink,
    tail: LogTail,
    *,
    mirror_stdout: bool = True,
) -> ProgressSink:
    """
    Build the `progress` callable phases receive.

    publish_event pushes a fully-formed event dict to the SSE queue.
    The same event is also mirrored into the log_tail as a formatted text line
    so late-attaching polling consumers still see activity.
    """
    def progress(
        phase: str,
        label: str,
        *,
        sub_phase: Optional[str] = None,
        current: Optional[int] = None,
        total: Optional[int] = None,
        detail: Optional[str] = None,
    ) -> None:
        ts = datetime.utcnow().isoformat(timespec="seconds")
        event: Dict[str, Any] = {
            "type": "progress",
            "phase": phase,
            "label": label,
            "ts": ts,
        }
        if sub_phase is not None:
            event["sub_phase"] = sub_phase
        if current is not None:
            event["current"] = current
        if total is not None:
            event["total"] = total
        if detail is not None:
            event["detail"] = detail

        # text mirror for LogTail + stdout
        parts = [f"phase {sub_phase or phase}", label]
        if current is not None and total is not None:
            parts.append(f"{current}/{total}")
        if detail is not None:
            parts.append(detail)
        stamp = datetime.utcnow().strftime("%H:%M:%S")
        text_line = f"[{stamp}] " + " · ".join(parts)
        tail.append(text_line)

        try:
            publish_event(event)
        except Exception:
            pass
        if mirror_stdout:
            print(text_line, flush=True)

    return progress


# ── stdlib-logging → SSE bridge for ported phases ──────────────────────────────

class _SSEForwardHandler(logging.Handler):
    """
    Logging handler that forwards every record to the current job's LogSink.
    Falls back to stdout when no sink is active (CLI / tests).
    """
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:
            return
        sink = _current_log_sink.get()
        if sink is not None:
            try:
                sink(msg)
            except Exception:
                pass
        else:
            print(msg, flush=True)


_PHASE_LOG_FMT = "%(name)s — %(message)s"
_phase_handler_installed = False


def _install_phase_handler() -> None:
    global _phase_handler_installed
    if _phase_handler_installed:
        return
    root = logging.getLogger("pipeline.phases")
    handler = _SSEForwardHandler()
    handler.setFormatter(logging.Formatter(_PHASE_LOG_FMT))
    handler.setLevel(logging.INFO)
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    root.propagate = False
    _phase_handler_installed = True


def get_phase_logger(name: str) -> logging.Logger:
    """
    Return a stdlib Logger namespaced under `pipeline.phases.<name>`.
    Records flow through `_SSEForwardHandler` which reads `_current_log_sink`
    on every emit and forwards to the active job's SSE log callback.
    """
    _install_phase_handler()
    return logging.getLogger(f"pipeline.phases.{name}")
