"""
routers/clusters.py — Thin HTTP layer for cluster endpoints.
All logic delegated to services/cluster_service.py.
"""
from typing import Optional
from fastapi import APIRouter, Query
from schemas.clusters import (
    ClusterOut, TicketOut, SweepRow,
    ServiceBreakdownRow, SourceMixOut,
)
from services import cluster_service

router = APIRouter(prefix="/clusters", tags=["clusters"])


@router.get("/{run_id}", response_model=list)
def list_clusters(
    run_id: str,
    source: Optional[str] = Query(default=None, description="Filter: 'incident' or 'workorder'"),
):
    """All clusters for a run, enriched with gap flags, LLM data, and source counts."""
    return cluster_service.fetch_clusters(run_id, source)


@router.get("/{run_id}/{cluster_id}/tickets", response_model=list)
def get_tickets(
    run_id: str,
    cluster_id: int,
    extra_ids: Optional[str] = Query(default=None, description="Comma-separated extra cluster IDs"),
):
    """Ticket detail for a cluster (including any merged duplicate clusters)."""
    ids = [cluster_id]
    if extra_ids:
        ids += [int(x) for x in extra_ids.split(",") if x.strip().isdigit()]
    return cluster_service.fetch_tickets(run_id, ids)


@router.get("/{run_id}/sweep", response_model=list)
def get_sweep(run_id: str):
    """Elbow curve and silhouette sweep data."""
    return cluster_service.fetch_sweep(run_id)


@router.get("/{run_id}/service-breakdown", response_model=list)
def get_service_breakdown(
    run_id: str,
    source: Optional[str] = Query(default=None),
):
    """Stacked bar data for the Service Breakdown tab."""
    return cluster_service.fetch_service_breakdown(run_id, source)


@router.get("/{run_id}/source-mix")
def get_source_mix(run_id: str):
    """Source mix data for the Source Mix tab."""
    return cluster_service.fetch_source_mix(run_id)
