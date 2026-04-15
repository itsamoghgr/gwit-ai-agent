"use client";

import { useEffect, useState } from "react";
import {
  ComposedChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ReferenceLine, ResponsiveContainer,
} from "recharts";
import type { SweepRow } from "@/lib/types";

export default function ElbowChart({ runId }: { runId: string }) {
  const [data, setData] = useState<SweepRow[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!runId) return;
    setLoading(true);
    fetch(`/api/clusters/${runId}/sweep`)
      .then(r => r.json())
      .then(d => { setData(Array.isArray(d) ? d : []); setLoading(false); })
      .catch(() => setLoading(false));
  }, [runId]);

  if (loading) return <div className="skeleton h-72 rounded-2xl w-full" />;
  if (data.length === 0) {
    return (
      <div className="flex items-center justify-center h-64 text-base-content/40 text-sm">
        Elbow / Silhouette data not available for this run.
      </div>
    );
  }

  const bestK = data.find(d => d.is_best_k);

  return (
    <div>
      {bestK && (
        <p className="text-sm text-base-content/60 mb-4">
          Best K selected: <strong className="text-primary">K = {bestK.k}</strong>
          {" "}(Silhouette = {bestK.silhouette.toFixed(4)})
        </p>
      )}
      <ResponsiveContainer width="100%" height={380}>
        <ComposedChart data={data} margin={{ top: 10, right: 70, bottom: 20, left: 20 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
          <XAxis
            dataKey="k"
            label={{ value: "K (# clusters)", position: "insideBottom", offset: -10, fontSize: 12 }}
            tick={{ fontSize: 11 }}
          />
          <YAxis
            yAxisId="left"
            label={{ value: "Inertia", angle: -90, position: "insideLeft", fontSize: 12 }}
            tick={{ fontSize: 11 }}
            tickFormatter={v => v >= 1000 ? `${(v / 1000).toFixed(0)}k` : v}
          />
          <YAxis
            yAxisId="right"
            orientation="right"
            label={{ value: "Silhouette", angle: 90, position: "insideRight", fontSize: 12 }}
            tick={{ fontSize: 11 }}
            domain={[0, 1]}
          />
          {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
          <Tooltip
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            formatter={(value: any, name: any) => [
              name === "Inertia"
                ? Number(value).toLocaleString(undefined, { maximumFractionDigits: 0 })
                : Number(value).toFixed(4),
              String(name),
            ]}
          />
          <Legend />
          {bestK && (
            <ReferenceLine
              x={bestK.k} yAxisId="left"
              stroke="#ef4444" strokeDasharray="6 3"
              label={{ value: `Best K=${bestK.k}`, fill: "#ef4444", fontSize: 11 }}
            />
          )}
          <Line yAxisId="left"  type="monotone" dataKey="inertia"    stroke="#3b82f6" name="Inertia"    dot={false} strokeWidth={2} />
          <Line yAxisId="right" type="monotone" dataKey="silhouette" stroke="#f59e0b" name="Silhouette" dot={false} strokeWidth={2} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
