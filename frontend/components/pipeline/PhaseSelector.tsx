"use client";

import type { PhaseInfo } from "@/lib/types";

interface Props {
  phases: PhaseInfo[];
  selected: Set<string>;
  onToggle: (phase: string) => void;
  onSelectAll: () => void;
  onClear: () => void;
  disabled?: boolean;
}

export default function PhaseSelector({
  phases, selected, onToggle, onSelectAll, onClear, disabled,
}: Props) {
  return (
    <div className="bg-base-100 border border-base-300 rounded-xl p-5">
      <div className="flex items-center justify-between mb-3">
        <p className="text-[11px] font-bold uppercase tracking-widest text-base-content/40">
          Phases
        </p>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={onSelectAll}
            disabled={disabled}
            className="text-[11px] text-primary hover:underline disabled:opacity-40"
          >
            Select all
          </button>
          <span className="text-base-content/20">·</span>
          <button
            type="button"
            onClick={onClear}
            disabled={disabled}
            className="text-[11px] text-base-content/60 hover:underline disabled:opacity-40"
          >
            Clear
          </button>
        </div>
      </div>
      <ul className="space-y-1.5">
        {phases.map(p => {
          const isOn = selected.has(p.phase);
          return (
            <li key={p.phase}>
              <label className={`flex items-start gap-3 p-2.5 rounded-lg cursor-pointer border
                ${isOn ? "bg-primary/5 border-primary/30" : "bg-base-100 border-base-300 hover:bg-base-200"}
                ${disabled ? "opacity-50 cursor-not-allowed" : ""}`}>
                <input
                  type="checkbox"
                  className="checkbox checkbox-sm checkbox-primary mt-0.5"
                  checked={isOn}
                  disabled={disabled}
                  onChange={() => onToggle(p.phase)}
                />
                <div>
                  <p className="text-[12px] font-semibold text-base-content">
                    Phase {p.phase}
                  </p>
                  <p className="text-[11px] text-base-content/55 leading-relaxed">
                    {p.description}
                  </p>
                </div>
              </label>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
