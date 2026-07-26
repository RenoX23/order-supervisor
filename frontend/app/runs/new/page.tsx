"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, EVENT_TYPES } from "@/lib/api";

export default function NewRunPage() {
  const router = useRouter();
  const [supervisorId, setSupervisorId] = useState("");
  const [orderId, setOrderId] = useState("");
  const [firstEvent, setFirstEvent] = useState("order_created");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Read ?supervisor= from the URL on the client (avoids the useSearchParams
  // Suspense requirement) and suggest a random order id.
  useEffect(() => {
    const sup = new URLSearchParams(window.location.search).get("supervisor");
    if (sup) setSupervisorId(sup);
    setOrderId(`order-${Math.random().toString(36).slice(2, 7)}`);
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const run = await api.createRun({
        supervisor_id: supervisorId.trim(),
        order_id: orderId.trim(),
        first_event: firstEvent,
      });
      router.push(`/runs/${run.id}`);
    } catch (err) {
      setError(String(err));
      setBusy(false);
    }
  }

  return (
    <div className="max-w-xl space-y-4">
      <h1 className="text-lg font-semibold">Start a run</h1>

      <form onSubmit={submit} className="space-y-4 rounded-lg border border-slate-800 bg-slate-900/50 p-4">
        <label className="block">
          <span className="mb-1 block text-sm text-slate-400">Supervisor ID</span>
          <input
            className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-xs"
            value={supervisorId}
            onChange={(e) => setSupervisorId(e.target.value)}
            placeholder="paste a supervisor id, or create one first"
            required
          />
          <span className="mt-1 block text-xs text-slate-500">
            No supervisor yet?{" "}
            <Link href="/supervisors/new" className="text-sky-400 hover:underline">
              Create one
            </Link>
            .
          </span>
        </label>

        <label className="block">
          <span className="mb-1 block text-sm text-slate-400">Order ID</span>
          <input
            className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm"
            value={orderId}
            onChange={(e) => setOrderId(e.target.value)}
            required
          />
        </label>

        <label className="block">
          <span className="mb-1 block text-sm text-slate-400">First event</span>
          <select
            className="w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm"
            value={firstEvent}
            onChange={(e) => setFirstEvent(e.target.value)}
          >
            {EVENT_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>

        <button
          disabled={busy}
          className="rounded bg-sky-600 px-3 py-2 text-sm font-medium hover:bg-sky-500 disabled:opacity-50"
        >
          {busy ? "Starting…" : "Start run (signal-with-start)"}
        </button>
      </form>

      {error && (
        <p className="rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">{error}</p>
      )}
    </div>
  );
}
