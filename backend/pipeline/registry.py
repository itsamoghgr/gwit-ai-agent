"""
pipeline/registry.py — Single source of truth for the phase catalog.

The service, router, and frontend all discover phases through this module.
Adding a new phase is a one-line edit here + a new file in phases/.
"""
from typing import Callable, Dict, List, Tuple

from pipeline.phases import (
    phase2_preprocessing,
    phase3_clustering,
    phase4_gap_analysis,
    phase5_kb_validation,
    phase6_kb_generation,
    phase7_validation,
)

PhaseFn = Callable[..., dict]

PHASES: Dict[str, Tuple[str, PhaseFn]] = {
    "2": ("Embedding Extraction & Quality Filtering",                phase2_preprocessing.run),
    "3": ("K-Means Clustering + LLM Relabel",                        phase3_clustering.run),
    "4": ("KB Gap Analysis",                                         phase4_gap_analysis.run),
    "5": ("LLM KB Coverage Validation & Cluster Dedup (all flags)",  phase5_kb_validation.run),
    "6": ("LLM KB Article Generation",                               phase6_kb_generation.run),
    "7": ("KB Article Validation",                                   phase7_validation.run),
}

DEFAULT_ORDER: List[str] = ["2", "3", "4", "5", "6", "7"]

# Table whose presence for a run_id means this phase can reuse the run.
# Used by create_job() when a single-phase run is launched without an explicit run_id.
PREREQ_TABLE: Dict[str, str] = {
    "3": "ticket_embeddings",
    "4": "clusters",
    "5": "gap_analysis",
    "6": "gap_analysis",
    "7": "generated_kb_articles",
}


def describe(phase: str) -> str:
    return PHASES[phase][0]


def fn(phase: str) -> PhaseFn:
    return PHASES[phase][1]
