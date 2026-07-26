"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, EVENT_TYPES, RunDetail, TimelineEntry } from "@/lib/api";
import { Panel, StatusBadge, TypeBadge } from "@/lib/ui";

const TERMINAL = ["completed", "terminated", "failed"];

function describe(e: TimelineEntry): string {
  const p = e.payload as Record<string, any>;
  switch (e.type) {
    case "event":
      return `event: ${p.type ?? p.trigger ?? "?"}`;
    case "wake_decision":
      if (p.control) return `control: ${p.control}${p.reason ? ` (${p.reason})` : ""}`;
      if (p.woke_agent)
        return `woke agent — trigger=${p.trigger}, events=${(p.event_types ?? []).join(", ") || "none"}`;
      return `did not wake on ${p.type} — ${p.reason ?? "benign"}`;
    case "agent_action":
      if (p.action_type) return `action → ${p.action_type}`;
      if (p.reasoning) return `reasoning: ${p.reasoning}`;
      return JSON.stringify(p);
    case "sleep_decision":
      if (p.complete_recommendation) return "agent recommends completion (workflow-gated)";
      return `sleep ${p.sleep_for_seconds}s → next wake ${p.next_wake_at ?? "?"}`;
    case "instruction":
      return `instruction: ${p.instruction}`;
    case "final_output":
      return `final summary: ${p.final_summary ?? ""}`;
    default:
      return JSON.stringify(p);
  }
}

export default function RunDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [run, setRun] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [eventType, setEventType] = useState<string>("payment_failed");
  const [instruction, setInstruction] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    return api
      .getRun(id)
      .then((r) => {
        setRun(r);
        setError(null);
      })
      .catch((e) => setError(String(e)));
  }, [id]);

  useEffect(() => {
    load();
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, [load]);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    try {
      await fn();
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  if (error && !run)
    return <p className="text-sm text-red-300">{error}</p>;
  if (!run) return <p className="text-sm text-slate-400">Loading…</p>;

  const terminal = TERMINAL.includes(run.status);
  const timeline = [...run.timeline].reverse(); // newest first

  return (
    <div className="space-y-4">
      {/* header */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-3">
          <h1 className="text-lg font-semibold">{run.order_id}</h1>
          <StatusBadge status={run.status} />
          <span className="text-xs text-slate-500">live</span>
        </div>
        <div className="text-right text-xs text-slate-500">
          <div>supervisor: {run.supervisor_name}</div>
          <div className="font-mono">{run.workflow_id}</div>
          {run.next_wake_at && <div>next wake: {new Date(run.next_wake_at).toLocaleTimeString()}</div>}
        </div>
      </div>

      {error && <p className="text-xs text-red-300">{error}</p>}

      {/* controls */}
      <Panel title="Controls">
        <div className="flex flex-wrap items-center gap-2">
          {run.status === "interrupted" ? (
            <button
              disabled={busy}
              onClick={() => act(() => api.resume(id))}
              className="rounded bg-emerald-600 px-3 py-1.5 text-sm hover:bg-emerald-500 disabled:opacity-50"
            >
              Resume
            </button>
          ) : (
            <button
              disabled={busy || terminal}
              onClick={() => act(() => api.interrupt(id))}
              className="rounded bg-amber-600 px-3 py-1.5 text-sm hover:bg-amber-500 disabled:opacity-50"
            >
              Interrupt
            </button>
          )}
          <button
            disabled={busy || terminal}
            onClick={() => act(() => api.terminate(id))}
            className="rounded bg-red-600 px-3 py-1.5 text-sm hover:bg-red-500 disabled:opacity-50"
          >
            Terminate
          </button>
          {terminal && <span className="text-xs text-slate-500">run is {run.status}</span>}
        </div>
      </Panel>

      <div className="grid gap-4 md:grid-cols-2">
        {/* inject event (also the event generator: fires any of the 9 types) */}
        <Panel title="Inject event">
          <div className="flex gap-2">
            <select
              className="flex-1 rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm"
              value={eventType}
              onChange={(e) => setEventType(e.target.value)}
            >
              {EVENT_TYPES.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <button
              disabled={busy || terminal}
              onClick={() => act(() => api.injectEvent(id, eventType))}
              className="rounded bg-sky-600 px-3 py-1.5 text-sm hover:bg-sky-500 disabled:opacity-50"
            >
              Send
            </button>
          </div>
        </Panel>

        {/* add instruction */}
        <Panel title="Add instruction">
          <div className="flex gap-2">
            <input
              className="flex-1 rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm"
              placeholder="e.g. VIP customer — prioritise"
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
            />
            <button
              disabled={busy || terminal || !instruction.trim()}
              onClick={() =>
                act(async () => {
                  await api.addInstruction(id, instruction.trim());
                  setInstruction("");
                })
              }
              className="rounded bg-sky-600 px-3 py-1.5 text-sm hover:bg-sky-500 disabled:opacity-50"
            >
              Send
            </button>
          </div>
        </Panel>
      </div>

      {/* compact memory */}
      <Panel title="Compact memory">
        <pre className="whitespace-pre-wrap text-sm text-slate-300">
          {run.memory_summary || "(empty)"}
        </pre>
      </Panel>

      {/* final summary */}
      {run.final_summary && (
        <Panel title="Final summary">
          <div className="space-y-2 text-sm">
            <p className="text-slate-200">{String(run.final_summary.final_summary ?? "")}</p>
            {Array.isArray(run.final_summary.key_learnings) && (
              <ul className="list-disc pl-5 text-slate-400">
                {(run.final_summary.key_learnings as string[]).map((k, i) => (
                  <li key={i}>{k}</li>
                ))}
              </ul>
            )}
            {run.final_summary.feedback ? (
              <p className="text-slate-400">
                <span className="text-slate-500">feedback: </span>
                {String(run.final_summary.feedback)}
              </p>
            ) : null}
          </div>
        </Panel>
      )}

      {/* timeline */}
      <Panel title={`Timeline (${run.timeline.length}) — latest first`}>
        <ol className="space-y-1.5">
          {timeline.map((e) => (
            <li key={e.id} className="flex items-start gap-2 border-b border-slate-800/60 pb-1.5 text-sm">
              <TypeBadge type={e.type} />
              <span className="text-slate-300">{describe(e)}</span>
              <span className="ml-auto shrink-0 text-[11px] text-slate-600">
                {new Date(e.created_at).toLocaleTimeString()}
              </span>
            </li>
          ))}
          {timeline.length === 0 && <li className="text-sm text-slate-500">No activity yet.</li>}
        </ol>
      </Panel>
    </div>
  );
}
