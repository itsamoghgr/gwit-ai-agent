"""
pipeline/constants.py — Thresholds and magic numbers shared across phases.
Keep all of these greppable in one place; the real ML ports (M1+) will read
from here rather than redeclaring them.
"""

SIM_COVERED           = 0.90
SIM_PARTIAL           = 0.87
SIM_GENERATED_MATCH   = 0.82
CENTROID_PREFILTER    = 0.88
GAP_NOVELTY_THRESHOLD = 0.87
CLUSTER_ALIGN_MIN     = 0.75

K_VALS = [10, 20, 30, 50, 75, 100, 125, 150, 175, 200]

TOP_KB_REMATCH = 20

EVIDENCE_TIER_WEIGHTS = {1: 3.0, 2: 1.5, 3: 0.5, 4: 0.0}

PRIORITY_WEIGHTS = {
    "volume":   0.35,
    "recency":  0.25,
    "gap":      0.25,
    "evidence": 0.15,
}

EMBEDDING_DIM = 1536

# Team-curated overrides applied at the end of phase 5 (port of
# app_gw-it/manual_flag_fixes.py). Each entry is (cluster_label_pattern, new_flag).
# Populated when manual_flag_fixes.py is ported in M2; empty until then.
MANUAL_FLAG_FIXES: list[tuple[str, str]] = []
