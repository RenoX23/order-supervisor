import React from "react";

const STATUS_COLORS: Record<string, string> = {
  pending: "bg-slate-700/40 text-slate-300 border-slate-600/40",
  running: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
  sleeping: "bg-sky-500/15 text-sky-300 border-sky-500/40",
  interrupted: "bg-amber-500/15 text-amber-300 border-amber-500/40",
  completed: "bg-indigo-500/15 text-indigo-300 border-indigo-500/40",
  terminated: "bg-slate-600/25 text-slate-400 border-slate-600/40",
  failed: "bg-red-500/15 text-red-300 border-red-500/40",
};

export function StatusBadge({ status }: { status: string }) {
  const c = STATUS_COLORS[status] ?? "bg-slate-700/40 text-slate-300 border-slate-600/40";
  return (
    <span className={`inline-block rounded border px-2 py-0.5 text-xs font-medium ${c}`}>
      {status}
    </span>
  );
}

const TYPE_COLORS: Record<string, string> = {
  event: "bg-sky-500/15 text-sky-300",
  agent_action: "bg-emerald-500/15 text-emerald-300",
  wake_decision: "bg-violet-500/15 text-violet-300",
  sleep_decision: "bg-slate-500/20 text-slate-300",
  instruction: "bg-amber-500/15 text-amber-300",
  final_output: "bg-indigo-500/15 text-indigo-300",
};

export function TypeBadge({ type }: { type: string }) {
  const c = TYPE_COLORS[type] ?? "bg-slate-600/30 text-slate-300";
  return <span className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${c}`}>{type}</span>;
}

export function Panel({ title, children }: { title?: string; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
      {title && <h2 className="mb-3 text-sm font-semibold text-slate-300">{title}</h2>}
      {children}
    </section>
  );
}
