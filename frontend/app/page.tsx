"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, RunSummary } from "@/lib/api";
import { StatusBadge } from "@/lib/ui";

export default function RunsListPage() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .listRuns()
        .then((r) => {
          if (alive) {
            setRuns(r);
            setError(null);
          }
        })
        .catch((e) => alive && setError(String(e)));
    load();
    const t = setInterval(load, 3000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Runs</h1>
        <Link
          href="/runs/new"
          className="rounded bg-sky-600 px-3 py-1.5 text-sm font-medium hover:bg-sky-500"
        >
          Start a run
        </Link>
      </div>

      {error && (
        <p className="rounded border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-300">
          {error} — is the API running on :8000?
        </p>
      )}

      <div className="overflow-hidden rounded-lg border border-slate-800">
        <table className="w-full text-sm">
          <thead className="bg-slate-900/70 text-left text-slate-400">
            <tr>
              <th className="px-4 py-2 font-medium">Order</th>
              <th className="px-4 py-2 font-medium">Status</th>
              <th className="px-4 py-2 font-medium">Supervisor</th>
              <th className="px-4 py-2 font-medium">Created</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.id} className="border-t border-slate-800 hover:bg-slate-900/40">
                <td className="px-4 py-2">
                  <Link href={`/runs/${r.id}`} className="text-sky-400 hover:underline">
                    {r.order_id}
                  </Link>
                </td>
                <td className="px-4 py-2">
                  <StatusBadge status={r.status} />
                </td>
                <td className="px-4 py-2 text-slate-300">{r.supervisor_name}</td>
                <td className="px-4 py-2 text-slate-500">
                  {new Date(r.created_at).toLocaleString()}
                </td>
              </tr>
            ))}
            {runs.length === 0 && !error && (
              <tr>
                <td colSpan={4} className="px-4 py-8 text-center text-slate-500">
                  No runs yet.{" "}
                  <Link href="/runs/new" className="text-sky-400 hover:underline">
                    Start one.
                  </Link>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
