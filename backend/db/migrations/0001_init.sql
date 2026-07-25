-- 0001_init.sql
-- Initial schema for the Order Supervisor POC.
-- Mirrors the data model in CLAUDE.md: `supervisors`, `runs`, `activity_log`.
-- gen_random_uuid() is built into PostgreSQL 13+ (no pgcrypto extension needed).

-- ── supervisors ──────────────────────────────────────────────────────────────
-- Reusable supervisor templates: a name, a base instruction, the actions it may
-- take, its wake policy, and model config.
CREATE TABLE supervisors (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name             TEXT        NOT NULL,
    base_instruction TEXT        NOT NULL,
    tools_enabled    JSONB       NOT NULL DEFAULT '[]'::jsonb,
    wake_policy      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    model_config     JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── runs ─────────────────────────────────────────────────────────────────────
-- One row per order run. `workflow_id` is unique because there is exactly one
-- Temporal workflow per order ("order-{order_id}").
CREATE TABLE runs (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    supervisor_id  UUID        NOT NULL REFERENCES supervisors(id) ON DELETE CASCADE,
    order_id       TEXT        NOT NULL,
    workflow_id    TEXT        NOT NULL UNIQUE,
    -- Lifecycle status. Expected values:
    --   pending | running | sleeping | interrupted | completed | terminated | failed
    status         TEXT        NOT NULL DEFAULT 'pending',
    memory_summary TEXT        NOT NULL DEFAULT '',
    next_wake_at   TIMESTAMPTZ,
    final_summary  JSONB,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_runs_supervisor_id ON runs (supervisor_id);
CREATE INDEX idx_runs_status        ON runs (status);
CREATE INDEX idx_runs_order_id      ON runs (order_id);

-- ── activity_log ─────────────────────────────────────────────────────────────
-- Single append-only log for everything that happens in a run: incoming events,
-- agent actions, wake/sleep decisions, manual instructions, and final output.
-- (Per the brief: one activity log, not a separate messages table.)
-- BIGINT identity id gives a stable, monotonic timeline order.
CREATE TABLE activity_log (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id     UUID        NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    type       TEXT        NOT NULL CHECK (type IN (
                   'event',
                   'agent_action',
                   'wake_decision',
                   'sleep_decision',
                   'instruction',
                   'final_output'
               )),
    payload    JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Timeline reads are always scoped to a run and ordered by insertion.
CREATE INDEX idx_activity_log_run_id ON activity_log (run_id, id);

-- ── keep runs.updated_at fresh ───────────────────────────────────────────────
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_runs_updated_at
    BEFORE UPDATE ON runs
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();
