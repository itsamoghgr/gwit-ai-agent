"""
app/embedding_utils.py
Reusable utilities for embedding extraction, combination, and similarity.
Used across Phase 2 (preprocessing), Phase 3 (clustering), Phase 4 (gap analysis).

Moved from src/ → app/ as app/ is the production module directory.
"""
import json
from typing import Optional, List, Tuple
import numpy as np
import pandas as pd
from sqlalchemy import text


# ── Vector Parsing ─────────────────────────────────────────────────────────────

def parse_jsonb_vector(val) -> Optional[np.ndarray]:
    """
    Parse a JSONB embedding value from PostgreSQL into a float32 NumPy array.

    PostgreSQL/psycopg2 can return JSONB columns as:
      - Python list (most common with psycopg2-binary)
      - JSON string
      - None (NULL)

    Returns None for NULL values so callers can handle missing embeddings explicitly.
    """
    if val is None:
        return None
    if isinstance(val, list):
        return np.array(val, dtype=np.float32)
    if isinstance(val, str):
        return np.array(json.loads(val), dtype=np.float32)
    # fallback (e.g., memoryview)
    return np.array(val, dtype=np.float32)


# ── Embedding Combination ──────────────────────────────────────────────────────

def combine_embeddings(
    primary: Optional[np.ndarray],
    secondary: Optional[np.ndarray],
    w_primary: float = 0.4,
    w_secondary: float = 0.6,
) -> Optional[np.ndarray]:
    """
    Combine two embedding vectors via weighted average.

    Strategy (for incident tickets):
      primary   = e_description         (100% available, short subject line)
      secondary = e_detailed_description (88.6% available, richer body text)

    When secondary is missing, falls back to primary only.
    When primary is missing, falls back to secondary only.
    Returns None only when both are None.
    """
    if primary is None and secondary is None:
        return None
    if primary is None:
        return secondary
    if secondary is None:
        return primary
    combined = w_primary * primary + w_secondary * secondary
    norm = np.linalg.norm(combined)
    if norm < 1e-10:
        return primary
    return combined / norm


# ── Batch Loading ──────────────────────────────────────────────────────────────

def load_incidents_batched(
    engine,
    batch_size: int = 5000,
    cols: Optional[List[str]] = None,
) -> pd.DataFrame:
    """
    Load the incidents_processed table in batches to avoid memory spikes.
    Returns a single concatenated DataFrame with all rows.
    """
    if cols is None:
        cols = [
            "id", "ticket_number", "service_type", "assigned_group",
            "assigned_support_company", "assigned_support_organization",
            "impact", "urgency", "reported_source",
            "reported_date", "resolved_date",
            "resolution_summary", "pii_detected",
            "e_description", "e_detailed_description", "e_resolution_summary",
        ]

    col_str = ", ".join(cols)
    with engine.connect() as conn:
        total = conn.execute(text("SELECT COUNT(*) FROM incidents_processed")).scalar()

    batches = []
    offset = 0
    while offset < total:
        q = f"SELECT {col_str} FROM incidents_processed ORDER BY id LIMIT {batch_size} OFFSET {offset}"
        batches.append(pd.read_sql(q, engine))
        offset += batch_size

    return pd.concat(batches, ignore_index=True)


# ── Matrix Extraction ──────────────────────────────────────────────────────────

def build_embedding_matrix(
    df: pd.DataFrame,
    e_primary_col: str,
    e_secondary_col: Optional[str] = None,
    w_primary: float = 0.4,
    w_secondary: float = 0.6,
    dim: int = 1536,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Parse and combine embedding columns from a DataFrame into a NumPy matrix.

    Returns:
        matrix  : (N, dim) float32 array
        valid   : (N,) bool array — True where embedding is valid
    """
    n = len(df)
    matrix = np.full((n, dim), np.nan, dtype=np.float32)
    valid  = np.zeros(n, dtype=bool)

    for i, (_, row) in enumerate(df.iterrows()):
        primary   = parse_jsonb_vector(row[e_primary_col])
        secondary = (
            parse_jsonb_vector(row[e_secondary_col])
            if e_secondary_col and e_secondary_col in df.columns
            else None
        )
        vec = combine_embeddings(primary, secondary, w_primary, w_secondary)
        if vec is not None:
            matrix[i] = vec
            valid[i]  = True

    return matrix, valid


# ── Cosine Similarity ──────────────────────────────────────────────────────────

def cosine_similarity_matrix(A: np.ndarray, B: np.ndarray) -> np.ndarray:
    """
    Pairwise cosine similarity between rows of A and rows of B.
    Assumes vectors are already L2-normalized (norm≈1.0).
    Returns (len(A), len(B)) float32 similarity matrix.
    """
    A_norms = np.linalg.norm(A, axis=1, keepdims=True)
    B_norms = np.linalg.norm(B, axis=1, keepdims=True)
    A_safe  = np.where(A_norms > 1e-10, A / A_norms, 0)
    B_safe  = np.where(B_norms > 1e-10, B / B_norms, 0)
    return (A_safe @ B_safe.T).astype(np.float32)


def max_similarity_to_kb(
    incident_matrix: np.ndarray,
    kb_matrix: np.ndarray,
    batch_size: int = 2000,
) -> np.ndarray:
    """
    For each incident vector, find the maximum cosine similarity to any KB article.
    Computed in batches to avoid creating a 58K × 391 matrix all at once.
    Returns (N,) float32 array.
    """
    n       = len(incident_matrix)
    max_sims = np.zeros(n, dtype=np.float32)
    for start in range(0, n, batch_size):
        end = min(start + batch_size, n)
        batch_sim = cosine_similarity_matrix(incident_matrix[start:end], kb_matrix)
        max_sims[start:end] = batch_sim.max(axis=1)
    return max_sims
