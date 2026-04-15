"use client";

import { useState } from "react";
import type { ClusterOut } from "@/lib/types";
import ClusterRow from "./ClusterRow";
import GapBadge from "@/components/GapBadge";

const ALL_FLAGS = ["CRITICAL", "PARTIAL", "COVERED", "DUPLICATE"];

interface Props {
  clusters: ClusterOut[];
  runId:    string;
  loading:  boolean;
}

export default function ClusterTable({ clusters, runId, loading }: Props) {
  const [flags, setFlags]   = useState<string[]>(["CRITICAL", "PARTIAL", "COVERED"]);
  const [search, setSearch] = useState("");

  function toggleFlag(f: string) {
    setFlags(prev => prev.includes(f) ? prev.filter(x => x !== f) : [...prev, f]);
  }

  let shown = flags.length ? clusters.filter(c => flags.includes(c.gap_flag)) : clusters;
  if (search.trim()) {
    const q = search.trim().toLowerCase();
    shown = shown.filter(c => c.cluster_label.toLowerCase().includes(q));
  }

  if (loading) {
    return (
      <div className="space-y-2">
        {Array(6).fill(0).map((_, i) => (
          <div key={i} className="skeleton h-12 rounded-2xl w-full" />
        ))}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Filter bar */}
      <div className="flex flex-wrap gap-4 items-center">
        <div className="flex items-center gap-3">
          <span className="text-xs text-base-content/50 font-semibold">Gap:</span>
          {ALL_FLAGS.map(f => (
            <label key={f} className="cursor-pointer flex items-center gap-1.5">
              <input
                type="checkbox"
                className="checkbox checkbox-xs checkbox-primary"
                checked={flags.includes(f)}
                onChange={() => toggleFlag(f)}
              />
              <GapBadge flag={f} size="sm" />
            </label>
          ))}
        </div>
        <input
          type="text"
          className="input input-sm input-bordered flex-1 max-w-xs"
          placeholder="Search label…"
          value={search}
          onChange={e => setSearch(e.target.value)}
        />
      </div>

      <p className="text-xs text-base-content/40">
        Showing <strong>{shown.length}</strong> of {clusters.length} clusters — expand any row to view tickets
      </p>

      {shown.length === 0 ? (
        <div className="flex items-center justify-center h-40 text-base-content/40 text-sm">
          No clusters match the current filters.
        </div>
      ) : (
        <div>
          {shown.map(c => (
            <ClusterRow key={c.cluster_id} cluster={c} allClusters={clusters} runId={runId} />
          ))}
        </div>
      )}
    </div>
  );
}
