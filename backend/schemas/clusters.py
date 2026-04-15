"""schemas/clusters.py — Pydantic response models for cluster endpoints."""
from typing import Optional
from pydantic import BaseModel


class ClusterOut(BaseModel):
    cluster_id:           int
    cluster_label:        str
    size:                 int
    gap_flag:             str
    max_kb_sim:           Optional[float]
    silhouette_score:     Optional[float]
    pca_x:                Optional[float]
    pca_y:                Optional[float]
    summary:              Optional[str]
    llm_kb_match:         Optional[str]
    llm_confidence:       Optional[str]
    llm_kb_reasoning:     Optional[str]
    canonical_cluster_id: Optional[int]
    wo_tickets:           int
    inc_tickets:          int


class TicketOut(BaseModel):
    cluster_id:      int
    source:          str
    ticket_number:   str
    service_type:    str
    assigned_group:  str
    problem_text:    str
    resolution_text: str


class SweepRow(BaseModel):
    k:          int
    inertia:    float
    silhouette: float
    is_best_k:  bool


class ServiceBreakdownRow(BaseModel):
    service_type: str
    gap_flag:     str
    tickets:      int


class SourceTotalRow(BaseModel):
    source:  str
    tickets: int


class SourcePerClusterRow(BaseModel):
    cluster_id:    int
    cluster_label: str
    source:        str
    tickets:       int


class WoStats(BaseModel):
    clusters_with_wo: int
    wo_dominant:      int
    total_clusters:   int


class SourceMixOut(BaseModel):
    overall:     list
    per_cluster: list
    wo_stats:    WoStats
