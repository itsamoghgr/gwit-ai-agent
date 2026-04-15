"""schemas/kb.py — Pydantic response models for KB endpoints."""
from typing import List, Optional
from pydantic import BaseModel


class KBArticleStats(BaseModel):
    canonical:    int
    duplicates:   int
    avg_quality:  float
    needs_review: int
    validated:    int


class KBArticleOut(BaseModel):
    cluster_id:        int
    title:             str
    category:          str
    quality_score:     float
    confidence:        str
    needs_review:      bool
    problem_statement: str
    symptoms:          Optional[List[str]]
    resolution_steps:  Optional[List[str]]
    additional_notes:  Optional[str]
    is_duplicate_of:   Optional[str]
    wo_in_cluster:     int


class KBArticlesResponse(BaseModel):
    stats:    KBArticleStats
    articles: list


class ExistingKBStats(BaseModel):
    total:       int
    active:      int
    orphaned:    int
    over_relied: int


class ExistingKBOut(BaseModel):
    id:          str
    title:       str
    issue:       str
    solution:    str
    util_status: Optional[str] = None
    util_count:  Optional[int] = None


class ExistingKBResponse(BaseModel):
    stats:    ExistingKBStats
    articles: list


class RunOut(BaseModel):
    run_id:     str
    started_at: str
    status:     str
