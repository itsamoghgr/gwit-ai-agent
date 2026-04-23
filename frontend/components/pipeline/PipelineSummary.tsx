"use client";

import Link from "next/link";
import { CheckCircle2, AlertTriangle, XCircle, ArrowRight } from "lucide-react";
import type { JobRunStatus } from "@/lib/types";

interface Props {
  status: JobRunStatus;
  runId: string;
  totalDurationS: number;
  failedPhases: string[];
  onViewRun: () => void;
}

export default function PipelineSummary({
  status, runId, totalDurationS, failedPhases, onViewRun,
}: Props) {
  const { icon, tone, label } =
    status === "complete"         ? { icon: <CheckCircle2 size={18} />, tone: "success", label: "Pipeline complete" } :
    status === "partial_failure"  ? { icon: <AlertTriangle size={18} />, tone: "warning", label: "Finished with errors" } :
    status === "failed"           ? { icon: <XCircle size={18} />,      tone: "error",   label: "Pipeline failed" } :
                                    { icon: <CheckCircle2 size={18} />, tone: "info",    label: status };

  return (
    <div className={`border rounded-xl p-5 bg-${tone}/5 border-${tone}/30`}>
      <div className="flex items-center gap-2 mb-2">
        <span className={`text-${tone}`}>{icon}</span>
        <p className="text-[14px] font-bold text-base-content">{label}</p>
      </div>
      <p className="text-[11px] text-base-content/55 font-mono mb-1">
        run_id: {runId}
      </p>
      <p className="text-[11px] text-base-content/55">
        Total duration: {totalDurationS.toFixed(1)}s
        {failedPhases.length > 0 && <> · Failed phases: {failedPhases.join(", ")}</>}
      </p>
      <Link
        href="/clusters"
        onClick={onViewRun}
        className="mt-3 inline-flex items-center gap-1.5 text-[12px] text-primary hover:underline"
      >
        View run in Clusters <ArrowRight size={12} />
      </Link>
    </div>
  );
}
