import { Activity, Ghost, TrendingUp, Radio } from "lucide-react";

const UTIL_CONFIG: Record<string, { Icon: typeof Activity; label: string; cls: string }> = {
  ACTIVE:        { Icon: Activity,   label: "Active",       cls: "text-success bg-success/10 border-success/25" },
  ORPHAN:        { Icon: Ghost,      label: "Orphan",       cls: "text-base-content/40 bg-base-200 border-base-300" },
  "OVER-RELIED": { Icon: TrendingUp, label: "Over-Relied",  cls: "text-error   bg-error/10   border-error/25"   },
  PERIPHERAL:    { Icon: Radio,      label: "Peripheral",   cls: "text-warning bg-warning/10 border-warning/25" },
};

interface Props {
  status: string;
  count:  number;
}

export default function UtilBadge({ status, count }: Props) {
  const cfg = UTIL_CONFIG[status];
  if (!cfg) return null;
  const { Icon, label, cls } = cfg;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md border text-[10px] font-semibold ${cls}`}>
      <Icon size={9} />
      {label}
      {count > 0 && <span className="opacity-50 font-normal">· {count}</span>}
    </span>
  );
}
