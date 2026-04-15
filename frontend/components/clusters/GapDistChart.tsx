"use client";

import {
  PieChart, Pie, Cell, Tooltip, Legend,
  BarChart, Bar, XAxis, YAxis, CartesianGrid, ResponsiveContainer,
} from "recharts";
import type { ClusterOut } from "@/lib/types";
import { GAP_COLORS } from "@/lib/types";

export default function GapDistChart({ clusters }: { clusters: ClusterOut[] }) {
  if (clusters.length === 0) {
    return <div className="flex items-center justify-center h-64 text-base-content/40 text-sm">No cluster data.</div>;
  }

  const agg: Record<string, { count: number; tickets: number }> = {};
  for (const c of clusters) {
    if (!agg[c.gap_flag]) agg[c.gap_flag] = { count: 0, tickets: 0 };
    agg[c.gap_flag].count++;
    agg[c.gap_flag].tickets += c.size;
  }

  const donutData = Object.entries(agg).map(([flag, { count }]) => ({ name: flag, value: count }));
  const barData   = Object.entries(agg).map(([flag, { tickets }]) => ({ flag, tickets }));

  return (
    <div className="grid grid-cols-2 gap-8">
      <div>
        <p className="text-sm font-semibold text-base-content/60 mb-3">Clusters by gap flag</p>
        <ResponsiveContainer width="100%" height={300}>
          <PieChart>
            <Pie
              data={donutData} cx="50%" cy="50%"
              innerRadius={75} outerRadius={110}
              dataKey="value"
              label={({ name, value }) => `${name}: ${value}`}
              labelLine={false}
            >
              {donutData.map((d, i) => (
                <Cell key={i} fill={GAP_COLORS[d.name] ?? "#6b7280"} />
              ))}
            </Pie>
            <Tooltip />
            <Legend />
          </PieChart>
        </ResponsiveContainer>
      </div>

      <div>
        <p className="text-sm font-semibold text-base-content/60 mb-3">Tickets by gap flag</p>
        <ResponsiveContainer width="100%" height={300}>
          <BarChart data={barData} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
            <XAxis dataKey="flag" tick={{ fontSize: 12 }} />
            <YAxis tickFormatter={v => v.toLocaleString()} tick={{ fontSize: 11 }} />
            <Tooltip formatter={(v: any) => [Number(v).toLocaleString(), "Tickets"]} />
            <Bar dataKey="tickets" radius={[6, 6, 0, 0]}>
              {barData.map((d, i) => (
                <Cell key={i} fill={GAP_COLORS[d.flag] ?? "#6b7280"} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
