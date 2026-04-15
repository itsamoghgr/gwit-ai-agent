"use client";

import { useEffect, useState } from "react";
import { useRun } from "@/lib/RunContext";
import StatCard from "@/components/StatCard";
import type { ClusterOut } from "@/lib/types";
import { GitBranch } from "lucide-react";

import ClusterTable  from "@/components/clusters/ClusterTable";
import PCAChart      from "@/components/clusters/PCAChart";
import SizesChart    from "@/components/clusters/SizesChart";
import GapDistChart  from "@/components/clusters/GapDistChart";
import ElbowChart    from "@/components/clusters/ElbowChart";
import ServiceChart  from "@/components/clusters/ServiceChart";
import SourceMixTab  from "@/components/clusters/SourceMixTab";

const TABS = [
  "Table", "PCA Centroids", "Cluster Sizes",
  "Gap Distribution", "Elbow / Silhouette",
  "Service Breakdown", "Source Mix",
];

const SOURCE_OPTIONS = ["All", "Incidents", "Work Orders"] as const;
type SourceFilter = typeof SOURCE_OPTIONS[number];
const SOURCE_MAP: Record<SourceFilter, string | undefined> = {
  "All": undefined, "Incidents": "incident", "Work Orders": "workorder",
};

export default function ClustersPage() {
  const { runId } = useRun();
  const [clusters, setClusters] = useState<ClusterOut[]>([]);
  const [loading, setLoading]   = useState(false);
  const [activeTab, setActiveTab] = useState(0);
  const [source, setSource]     = useState<SourceFilter>("All");

  useEffect(() => {
    if (!runId) return;
    setLoading(true);
    const src = SOURCE_MAP[source];
    const url = src ? `/api/clusters/${runId}?source=${src}` : `/api/clusters/${runId}`;
    fetch(url)
      .then(r => r.json())
      .then(d => { setClusters(Array.isArray(d) ? d : []); setLoading(false); })
      .catch(() => setLoading(false));
  }, [runId, source]);

  if (!runId) {
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-3 text-base-content/30">
        <GitBranch size={36} strokeWidth={1.5} />
        <p className="text-sm">Select a pipeline run from the sidebar to explore clusters.</p>
      </div>
    );
  }

  const c = loading ? { total: 0, critical: 0, partial: 0, covered: 0, duplicate: 0 } : {
    total:     clusters.length,
    critical:  clusters.filter(x => x.gap_flag === "CRITICAL").length,
    partial:   clusters.filter(x => x.gap_flag === "PARTIAL").length,
    covered:   clusters.filter(x => x.gap_flag === "COVERED").length,
    duplicate: clusters.filter(x => x.gap_flag === "DUPLICATE").length,
  };

  return (
    <div>
      {/* Page header */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-base-content tracking-tight">Clusters</h1>
        <p className="text-sm text-base-content/45 mt-0.5">
          Clustering results, gap analysis, and ticket breakdown for the selected run.
        </p>
      </div>

      {/* KPI strip */}
      <div className="grid grid-cols-5 gap-3 mb-6">
        {loading ? Array(5).fill(0).map((_, i) => (
          <div key={i} className="h-[76px] rounded-xl bg-base-200 animate-pulse border border-base-300" />
        )) : <>
          <StatCard label="Total Clusters" value={c.total.toLocaleString()} />
          <StatCard label="Critical"  value={c.critical.toLocaleString()}  accent="critical" sub="Need new KB article" />
          <StatCard label="Partial"   value={c.partial.toLocaleString()}   accent="warning"  sub="KB needs updating" />
          <StatCard label="Covered"   value={c.covered.toLocaleString()}   accent="success"  sub="Adequately covered" />
          <StatCard label="Duplicate" value={c.duplicate.toLocaleString()} sub="Merged into parent" />
        </>}
      </div>

      {/* Source filter */}
      <div className="flex items-center gap-1.5 mb-5">
        <span className="text-[10px] font-bold uppercase tracking-widest text-base-content/30 mr-1">Source</span>
        {SOURCE_OPTIONS.map(opt => (
          <button
            key={opt}
            onClick={() => setSource(opt)}
            className={`px-3 py-1 rounded-lg text-xs font-medium transition-all ${
              source === opt
                ? "bg-primary text-primary-content shadow-sm"
                : "bg-base-200 text-base-content/50 hover:text-base-content border border-base-300"
            }`}
          >{opt}</button>
        ))}
      </div>

      {/* Tab bar */}
      <div className="flex gap-0 border-b border-base-300 mb-5">
        {TABS.map((t, i) => (
          <button
            key={t}
            onClick={() => setActiveTab(i)}
            className={`px-4 py-2.5 text-[12px] font-medium whitespace-nowrap border-b-2 transition-all ${
              activeTab === i
                ? "border-primary text-primary"
                : "border-transparent text-base-content/40 hover:text-base-content"
            }`}
          >{t}</button>
        ))}
      </div>

      {/* Tab content */}
      <div className="bg-base-100 border border-base-300 rounded-xl">
        <div className="p-5">
          {activeTab === 0 && <ClusterTable clusters={clusters} runId={runId} loading={loading} />}
          {activeTab === 1 && <PCAChart clusters={clusters} />}
          {activeTab === 2 && <SizesChart clusters={clusters} />}
          {activeTab === 3 && <GapDistChart clusters={clusters} />}
          {activeTab === 4 && <ElbowChart runId={runId} />}
          {activeTab === 5 && <ServiceChart runId={runId} source={SOURCE_MAP[source]} />}
          {activeTab === 6 && <SourceMixTab runId={runId} />}
        </div>
      </div>
    </div>
  );
}
