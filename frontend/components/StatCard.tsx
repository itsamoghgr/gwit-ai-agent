interface Props {
  label: string;
  value: string | number;
  accent?: "critical" | "warning" | "success";
  sub?: string;
}

const ACCENT_VALUE: Record<string, string> = {
  critical: "text-error",
  warning:  "text-warning",
  success:  "text-success",
};

export default function StatCard({ label, value, accent, sub }: Props) {
  const valueClass = accent ? ACCENT_VALUE[accent] : "text-base-content";
  return (
    <div className="bg-base-100 border border-base-300 rounded-xl p-4 flex flex-col gap-1">
      <p className="text-[9px] font-bold uppercase tracking-widest text-base-content/35">
        {label}
      </p>
      <p className={`text-[26px] font-bold tracking-tight leading-none ${valueClass}`}>
        {value}
      </p>
      {sub && (
        <p className="text-[10px] text-base-content/30 leading-tight">{sub}</p>
      )}
    </div>
  );
}
