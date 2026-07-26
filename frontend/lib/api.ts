// Typed client for the FastAPI backend. All calls hit the real API (CORS-enabled).
export const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export const EVENT_TYPES = [
  "order_created",
  "payment_confirmed",
  "payment_failed",
  "shipment_created",
  "shipment_delayed",
  "delivered",
  "refund_requested",
  "customer_message_received",
  "no_update_for_n_hours",
] as const;

export const ACTION_TYPES = [
  "message_fulfillment_team",
  "message_payments_team",
  "message_logistics_team",
  "message_customer",
  "create_internal_note",
] as const;

export interface RunSummary {
  id: string;
  order_id: string;
  workflow_id: string;
  status: string;
  supervisor_name: string;
  next_wake_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface TimelineEntry {
  id: number;
  type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface RunDetail extends RunSummary {
  supervisor_id: string;
  memory_summary: string;
  final_summary: Record<string, unknown> | null;
  timeline: TimelineEntry[];
}

export interface Supervisor {
  id: string;
  name: string;
  base_instruction: string;
  tools_enabled: string[];
  wake_policy: Record<string, unknown>;
  model_config: Record<string, unknown>;
  created_at: string;
}

async function req<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}/api${path}`, {
    headers: { "content-type": "application/json" },
    cache: "no-store",
    ...opts,
  });
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  const text = await res.text();
  return (text ? JSON.parse(text) : null) as T;
}

export const api = {
  createSupervisor: (body: {
    name: string;
    base_instruction: string;
    wake_policy?: Record<string, unknown>;
  }) => req<Supervisor>("/supervisors", { method: "POST", body: JSON.stringify(body) }),

  createRun: (body: { supervisor_id: string; order_id: string; first_event?: string }) =>
    req<RunDetail>("/runs", { method: "POST", body: JSON.stringify(body) }),

  listRuns: () => req<RunSummary[]>("/runs"),
  getRun: (id: string) => req<RunDetail>(`/runs/${id}`),

  injectEvent: (id: string, type: string, payload: Record<string, unknown> = {}) =>
    req(`/runs/${id}/events`, { method: "POST", body: JSON.stringify({ type, payload }) }),
  addInstruction: (id: string, text: string) =>
    req(`/runs/${id}/instructions`, { method: "POST", body: JSON.stringify({ text }) }),
  interrupt: (id: string) => req(`/runs/${id}/interrupt`, { method: "POST" }),
  resume: (id: string) => req(`/runs/${id}/resume`, { method: "POST" }),
  terminate: (id: string, reason = "manual") =>
    req(`/runs/${id}/terminate`, { method: "POST", body: JSON.stringify({ reason }) }),
};
