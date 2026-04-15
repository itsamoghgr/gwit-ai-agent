"use client";

import { ScatterChart, Scatter, XAxis, YAxis, ZAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from "recharts";
import type { ClusterOut } from "@/lib/types";
import { GAP_COLORS } from "@/lib/types";

interface Props { clusters: ClusterOut[]; }

export default function PCAChart({ clusters }: Props) {
  const valid = clusters.filter(c => c.pca_x !== null && c.pca_y !== null);

  if (valid.length === 0) {
    return (
      <div className="flex items-center justify-center h-64 text-base-content/40 text-sm">
        PCA coordinates not available. Run Phase 3 first.
      </div>
    );
  }

  const byFlag: Record<string, any[]> = {};
  for (const c of valid) {
    if (!byFlag[c.gap_flag]) byFlag[c.gap_flag] = [];
    byFlag[c.gap_flag].push({ x: c.pca_x, y: c.pca_y, z: c.size, label: c.cluster_label });
  }

  return (
    <div>
      <p className="text-sm text-base-content/50 mb-4">Cluster centroids projected to 2D — bubble size = ticket count</p>
      <ResponsiveContainer width="100%" height={440}>
        <ScatterChart margin={{ top: 20, right: 30, bottom: 20, left: 20 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
          <XAxis dataKey="x" type="number" name="PCA X" tick={{ fontSize: 11 }} />
          <YAxis dataKey="y" type="number" name="PCA Y" tick={{ fontSize: 11 }} />
          <ZAxis dataKey="z" range={[40, 800]} name="Tickets" />
          <Tooltip
            content={({ payload }) => {
              if (!payload?.length) return null;
              const d = payload[0].payload;
              return (
                <div className="bg-base-100 border border-base-300 rounded-2xl p-3 text-xs shadow-lg max-w-56">
                  <p className="font-semibold text-base-content leading-tight">{d.label}</p>
                  <p className="text-base-content/50 mt-1">{d.z.toLocaleString()} tickets</p>
                </div>
              );
            }}
          />
          <Legend />
          {Object.entries(byFlag).map(([flag, data]) => (
            <Scatter key={flag} name={flag} data={data} fill={GAP_COLORS[flag] ?? "#6b7280"} fillOpacity={0.75} />
          ))}
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  );
}
