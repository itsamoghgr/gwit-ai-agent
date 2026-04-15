"use client";

import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Cell, ResponsiveContainer } from "recharts";
import type { ClusterOut } from "@/lib/types";
import { GAP_COLORS } from "@/lib/types";

export default function SizesChart({ clusters }: { clusters: ClusterOut[] }) {
  if (clusters.length === 0) {
    return <div className="flex items-center justify-center h-64 text-base-content/40 text-sm">No cluster data.</div>;
  }

  const data = [...clusters]
    .sort((a, b) => b.size - a.size)
    .slice(0, 30)
    .reverse() // ascending so largest is at top in horizontal layout
    .map(c => ({
      label: c.cluster_label.length > 42 ? c.cluster_label.slice(0, 42) + "…" : c.cluster_label,
      size: c.size,
      gap_flag: c.gap_flag,
    }));

  return (
    <div>
      <p className="text-sm text-base-content/50 mb-4">Top 30 clusters by ticket volume, coloured by gap flag</p>
      <ResponsiveContainer width="100%" height={Math.max(420, data.length * 28)}>
        <BarChart data={data} layout="vertical" margin={{ top: 5, right: 40, bottom: 5, left: 200 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" horizontal={false} />
          <XAxis type="number" tick={{ fontSize: 11 }} tickFormatter={v => v.toLocaleString()} />
          <YAxis type="category" dataKey="label" width={195} tick={{ fontSize: 11 }} />
          <Tooltip formatter={(v: any) => [Number(v).toLocaleString(), "Tickets"]} />
          <Bar dataKey="size" radius={[0, 6, 6, 0]}>
            {data.map((d, i) => (
              <Cell key={i} fill={GAP_COLORS[d.gap_flag] ?? "#6b7280"} fillOpacity={0.85} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
