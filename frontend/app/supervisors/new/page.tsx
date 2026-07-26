"use client";

import Link from "next/link";
import { useState } from "react";
import { api, ACTION_TYPES, Supervisor } from "@/lib/api";

// How aggressively the agent wakes. "aggressive" wakes the main agent on every
// event; "balanced" uses the rule-based classifier (wakes on important events,
// logs benign ones like payment_confirmed and keeps sleeping). Maps to
// wake_policy.mode, which the backend policy actually reads.
const WAKE_MODES = [
  {
    value: "balanced",
    label: "Balanced",
    hint: "Classifier decides — wake on important events, log benign ones and keep sleeping.",
  },
  {
    value: "aggressive",
    label: "Aggressive",
    hint: "Wake the agent on every incoming event, even benign progress updates.",
  },
] as const;

export default function NewSupervisorPage() {
  const [name, setName] = useState("Order Supervisor");
  const [instruction, setInstruction] = useState(
    "Watch this order from creation to completion. Resolve payment and shipping issues promptly, keep the customer informed, and escalate to the right team when needed."
  );
  const [sleep, setSleep] = useState(60);
  const [wakeMode, setWakeMode] = useState<string>("balanced");
  const [tools, setTools] = useState<string[]>([...ACTION_TYPES]);
  const [created, setCreated] = useState<Supervisor | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function toggleTool(t: string) {
    setTools((prev) => (prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t]));
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (tools.length === 0) {
      setError("Enable at least one action.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const sup = await api.createSupervisor({
        name,
        base_instruction: instruction,
        tools_enabled: tools,
        wake_policy: {
          default_sleep_seconds: sleep,
          max_age_seconds: 86400,
          mode: wakeMode,
        },
      });
      setCreated(sup);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="max-w-xl space-y-4">
      <h1 className="text-lg font-semibold">New supervisor</h1>

      <form onSubmit={submit} className="space-y-4 rounded-lg border border-slate-800 bg-slate-900/50 p-4">
        <label className="block">
          <span className="mb-1 block text-sm text-slate-400">Name</span>
          <input
            className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
        </label>

        <label className="block">
          <span className="mb-1 block text-sm text-slate-400">Base instruction</span>
          <textarea
            className="h-28 w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm"
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            required
          />
        </label>

        {/* Available actions — the tools this supervisor may call. Enforced on the
            agent: the model can only pick from the enabled set. */}
        <fieldset className="block">
          <legend className="mb-1 block text-sm text-slate-400">Available actions</legend>
          <div className="grid gap-1.5 sm:grid-cols-2">
            {ACTION_TYPES.map((t) => (
              <label
                key={t}
                className="flex cursor-pointer items-center gap-2 rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs"
              >
                <input
                  type="checkbox"
                  className="accent-sky-500"
                  checked={tools.includes(t)}
                  onChange={() => toggleTool(t)}
                />
                <span className="font-mono">{t}</span>
              </label>
            ))}
          </div>
          <span className="mt-1 block text-xs text-slate-500">
            The agent may only take the actions you enable here.
          </span>
        </fieldset>

        {/* Wake aggressiveness — optional guidance for how eagerly the agent wakes. */}
        <fieldset className="block">
          <legend className="mb-1 block text-sm text-slate-400">Wake aggressiveness</legend>
          <div className="grid gap-1.5 sm:grid-cols-2">
            {WAKE_MODES.map((m) => (
              <label
                key={m.value}
                className={`cursor-pointer rounded border px-3 py-2 text-xs ${
                  wakeMode === m.value
                    ? "border-sky-500/60 bg-sky-500/10"
                    : "border-slate-700 bg-slate-950"
                }`}
              >
                <span className="flex items-center gap-2">
                  <input
                    type="radio"
                    name="wakeMode"
                    className="accent-sky-500"
                    checked={wakeMode === m.value}
                    onChange={() => setWakeMode(m.value)}
                  />
                  <span className="font-medium text-slate-200">{m.label}</span>
                </span>
                <span className="mt-1 block text-slate-500">{m.hint}</span>
              </label>
            ))}
          </div>
        </fieldset>

        <label className="block">
          <span className="mb-1 block text-sm text-slate-400">Default sleep (seconds between scheduled wake-ups)</span>
          <input
            type="number"
            min={5}
            className="w-40 rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm"
            value={sleep}
            onChange={(e) => setSleep(Number(e.target.value))}
          />
        </label>

        <button
          disabled={busy}
          className="rounded bg-sky-600 px-3 py-2 text-sm font-medium hover:bg-sky-500 disabled:opacity-50"
        >
          {busy ? "Creating…" : "Create supervisor"}
        </button>
      </form>

      {error && (
        <p className="rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">{error}</p>
      )}

      {created && (
        <div className="space-y-2 rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-4 text-sm">
          <p className="text-emerald-300">Created supervisor.</p>
          <p className="font-mono text-xs text-slate-400">{created.id}</p>
          <Link
            href={`/runs/new?supervisor=${created.id}`}
            className="inline-block rounded bg-sky-600 px-3 py-1.5 font-medium hover:bg-sky-500"
          >
            Start a run with this supervisor →
          </Link>
        </div>
      )}
    </div>
  );
}
