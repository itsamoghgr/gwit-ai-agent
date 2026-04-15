"use client";

import { useEffect, useState } from "react";
import { PieChart, Pie, Cell, Tooltip, Legend, BarChart, Bar, XAxis, YAxis, CartesianGrid, ResponsiveContainer } from "recharts";

const SRC_COLORS: Record<string, string> = {
  incident:  "#3b82f6",
  workorder: "#f97316",
};

export default function SourceMixTab({ runId }: { runId: string }) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!runId) return;
    setLoading(true);
    fetch(`/api/clusters/${runId}/source-mix`)
      .then(r => r.json())
      .then(d => { setData(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, [runId]);

  if (loading) return <div className="skeleton h-72 rounded-2xl w-full" />;
  if (!data) return <div className="flex items-center justify-center h-64 text-base-content/40 text-sm">No source data.</div>;

  const { overall, per_cluster, wo_stats } = data;

  // Build per-cluster stacked bar (top 30 by total)
  const clusterMap: Record<string, { label: string; incident: number; workorder: number }> = {};
  for (const row of per_cluster as any[]) {
    if (!clusterMap[row.cluster_id]) {
      clusterMap[row.cluster_id] = { label: row.cluster_label.slice(0, 38), incident: 0, workorder: 0 };
    }
    const key = row.source as "incident" | "workorder";
    if (key in SRC_COLORS) clusterMap[row.cluster_id][key] = row.tickets;
  }

  const top30 = Object.values(clusterMap)
    .sort((a, b) => (b.incident + b.workorder) - (a.incident + a.workorder))
    .slice(0, 30)
    .reverse();

  const incTotal = wo_stats.total_clusters - wo_stats.wo_dominant;

  return (
    <div className="space-y-6">
      {/* WO summary stats */}
      <div className="grid grid-cols-3 gap-4">
        {[
          { label: "Clusters with Work Orders", value: wo_stats.clusters_with_wo, sub: `of ${wo_stats.total_clusters} total` },
          { label: "WO-Dominant Clusters",      value: wo_stats.wo_dominant,       sub: ">50% tickets are Work Orders" },
          { label: "Incident-Dominant Clusters", value: incTotal,                  sub: "Incidents majority" },
        ].map(({ label, value, sub }) => (
          <div key={label} className="bg-base-200 rounded-2xl p-4 text-center">
            <p className="text-xs text-base-content/50 mb-1">{label}</p>
            <p className="text-2xl font-bold">{value.toLocaleString()}</p>
            <p className="text-xs text-base-content/40 mt-0.5">{sub}</p>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-3 gap-6">
        {/* Overall donut */}
        <div>
          <p className="text-sm font-semibold text-base-content/60 mb-3">Overall source mix</p>
          <ResponsiveContainer width="100%" height={230}>
            <PieChart>
              <Pie
                data={(overall as any[]).map(r => ({ name: r.source, value: r.tickets }))}
                cx="50%" cy="50%" innerRadius={58} outerRadius={88}
                dataKey="value"
                label={({ name, percent }: { name?: string; percent?: number }) =>
                  `${name ?? ""} ${((percent ?? 0) * 100).toFixed(0)}%`
                }
                labelLine={false}
              >
                {(overall as any[]).map((r, i) => (
                  <Cell key={i} fill={SRC_COLORS[r.source] ?? "#6b7280"} />
                ))}
              </Pie>
              <Tooltip formatter={(v: any) => [Number(v).toLocaleString(), "Tickets"]} />
              <Legend />
            </PieChart>
          </ResponsiveContainer>
        </div>

        {/* Top 30 stacked bar */}
        <div className="col-span-2">
          <p className="text-sm font-semibold text-base-content/60 mb-3">Top 30 clusters — Incidents vs Work Orders</p>
          <ResponsiveContainer width="100%" height={Math.max(360, top30.length * 22)}>
            <BarChart data={top30} layout="vertical" margin={{ top: 5, right: 20, bottom: 5, left: 165 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" horizontal={false} />
              <XAxis type="number" tickFormatter={v => v.toLocaleString()} tick={{ fontSize: 10 }} />
              <YAxis type="category" dataKey="label" width={160} tick={{ fontSize: 10 }} />
              <Tooltip formatter={(v: any) => [Number(v).toLocaleString(), ""]} />
              <Legend />
              <Bar dataKey="incident"  stackId="a" fill={SRC_COLORS.incident}  name="Incidents"    />
              <Bar dataKey="workorder" stackId="a" fill={SRC_COLORS.workorder} name="Work Orders" radius={[0, 6, 6, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}
