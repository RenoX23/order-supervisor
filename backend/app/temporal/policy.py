"""Lightweight, rule-based wake policy — the "classifier".

Given an incoming event type, decide whether it is important enough to wake the
full reasoning agent now, or whether it should merely be logged while the
workflow keeps sleeping until its next scheduled wake-up.

Deterministic and dependency-free on purpose, so it is safe to call directly
from workflow code (no activity round-trip needed for a simple allow/deny list).
"""

from __future__ import annotations

from dataclasses import dataclass

# Events that always warrant waking the agent immediately.
DEFAULT_WAKE_EVENTS: frozenset[str] = frozenset({
    "order_created",           # first touch — establish a plan
    "payment_failed",
    "shipment_delayed",
    "delivered",               # terminal — react and wrap up
    "refund_requested",
    "customer_message_received",
    "no_update_for_n_hours",   # scheduled nudge — re-evaluate
})

# Benign progress updates: record them, but don't wake the agent.
DEFAULT_LOG_ONLY_EVENTS: frozenset[str] = frozenset({
    "payment_confirmed",
    "shipment_created",
})


@dataclass
class WakeAssessment:
    wake: bool
    reason: str


def classify(event_type: str, wake_policy: dict | None = None) -> WakeAssessment:
    """Decide whether ``event_type`` should wake the main agent.

    ``wake_policy`` (from the supervisor config) may tune behaviour:
      - ``mode``: ``"aggressive"`` wakes on everything; anything else uses the lists.
      - ``wake_events`` / ``log_only_events``: override the default sets.
    """
    policy = wake_policy or {}
    mode = str(policy.get("mode", "")).lower()

    wake_events = set(policy.get("wake_events", DEFAULT_WAKE_EVENTS))
    log_only = set(policy.get("log_only_events", DEFAULT_LOG_ONLY_EVENTS))

    if mode == "aggressive":
        return WakeAssessment(True, f"aggressive mode: waking on '{event_type}'")

    if event_type in wake_events:
        return WakeAssessment(True, f"'{event_type}' is a wake event")
    if event_type in log_only:
        return WakeAssessment(False, f"'{event_type}' is benign; logged only")

    # Unknown event types: err on the side of waking (unknown-event escalation).
    return WakeAssessment(True, f"'{event_type}' is unknown; waking to be safe")
