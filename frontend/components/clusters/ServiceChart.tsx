"use client";

import { useEffect, useState } from "react";
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, Cell, ResponsiveContainer } from "recharts";
import { GAP_COLORS } from "@/lib/types";

interface ServiceRow { service_type: string; gap_flag: string; tickets: number; }

export default function ServiceChart({ runId, source }: { runId: string; source?: string }) {
  const [data, setData] = useState<ServiceRow[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!runId) return;
    setLoading(true);
    const qs = source ? `?source=${source}` : "";
    fetch(`/api/clusters/${runId}/service-breakdown${qs}`)
      .then(r => r.json())
      .then(d => { setData(Array.isArray(d) ? d : []); setLoading(false); })
      .catch(() => setLoading(false));
  }, [runId, source]);

  if (loading) return <div className="skeleton h-64 rounded-2xl w-full" />;
  if (data.length === 0) {
    return <div className="flex items-center justify-center h-64 text-base-content/40 text-sm">No service breakdown data.</div>;
  }

  const services = [...new Set(data.map(d => d.service_type))];
  const flags    = [...new Set(data.map(d => d.gap_flag))];

  // Pivot to stacked format
  const pivoted = services.map(svc => {
    const row: Record<string, string | number> = { service_type: svc };
    for (const flag of flags) {
      const found = data.find(d => d.service_type === svc && d.gap_flag === flag);
      row[flag] = found?.tickets ?? 0;
    }
    return row;
  }).sort((a, b) => {
    const aT = flags.reduce((s, f) => s + ((a[f] as number) || 0), 0);
    const bT = flags.reduce((s, f) => s + ((b[f] as number) || 0), 0);
    return aT - bT; // ascending for horizontal bar
  });

  return (
    <div>
      <p className="text-sm text-base-content/50 mb-4">Ticket volume per service type, stacked by gap flag</p>
      <ResponsiveContainer width="100%" height={Math.max(420, services.length * 30)}>
        <BarChart data={pivoted} layout="vertical" margin={{ top: 5, right: 40, bottom: 5, left: 220 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" horizontal={false} />
          <XAxis type="number" tickFormatter={v => v.toLocaleString()} tick={{ fontSize: 11 }} />
          <YAxis type="category" dataKey="service_type" width={215} tick={{ fontSize: 10 }} />
          <Tooltip formatter={(v: any) => [Number(v).toLocaleString(), ""]} />
          <Legend />
          {flags.map(flag => (
            <Bar key={flag} dataKey={flag} stackId="a" name={flag}
              fill={GAP_COLORS[flag] ?? "#6b7280"} fillOpacity={0.85}
              radius={flag === flags[flags.length - 1] ? [0, 6, 6, 0] : [0, 0, 0, 0]}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
