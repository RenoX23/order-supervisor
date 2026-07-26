"use client";

import Link from "next/link";
import { useState } from "react";
import { api, Supervisor } from "@/lib/api";

export default function NewSupervisorPage() {
  const [name, setName] = useState("Order Supervisor");
  const [instruction, setInstruction] = useState(
    "Watch this order from creation to completion. Resolve payment and shipping issues promptly, keep the customer informed, and escalate to the right team when needed."
  );
  const [sleep, setSleep] = useState(60);
  const [created, setCreated] = useState<Supervisor | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const sup = await api.createSupervisor({
        name,
        base_instruction: instruction,
        wake_policy: { default_sleep_seconds: sleep, max_age_seconds: 86400 },
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
