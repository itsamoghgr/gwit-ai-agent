"""
pipeline/ — Self-contained data pipeline for the GW IT app.

Public surface:
    from pipeline.registry import PHASES, DEFAULT_ORDER, PREREQ_TABLE

Each phase module exposes one function:
    run(engine, run_id: str, *, log: Callable[[str], None]) -> dict

Phases are pure library code — no FastAPI, no HTTP. The orchestrator lives in
services/pipeline_service.py and wires phase output into SSE events.
"""
