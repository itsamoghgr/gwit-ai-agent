import { AlertCircle, AlertTriangle, CheckCircle, Copy } from "lucide-react";

const GAP_CONFIG: Record<string, { Icon: typeof AlertCircle; cls: string }> = {
  CRITICAL:  { Icon: AlertCircle,   cls: "text-error   bg-error/10   border-error/25"   },
  PARTIAL:   { Icon: AlertTriangle, cls: "text-warning bg-warning/10 border-warning/25" },
  COVERED:   { Icon: CheckCircle,   cls: "text-success bg-success/10 border-success/25" },
  DUPLICATE: { Icon: Copy,          cls: "text-base-content/40 bg-base-200 border-base-300" },
};

interface Props {
  flag: string;
  size?: "sm" | "md";
}

export default function GapBadge({ flag, size = "md" }: Props) {
  const cfg = GAP_CONFIG[flag];
  if (!cfg) return null;
  const { Icon, cls } = cfg;
  const iconSz  = size === "sm" ? 9  : 11;
  const textCls = size === "sm" ? "text-[9px]" : "text-[10px]";
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md border font-semibold ${cls} ${textCls}`}>
      <Icon size={iconSz} />
      {flag}
    </span>
  );
}
