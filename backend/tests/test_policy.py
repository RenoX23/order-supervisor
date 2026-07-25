"""Unit tests for the rule-based wake classifier."""

from app.temporal import policy


def test_wake_events_wake_the_agent():
    for et in ("payment_failed", "shipment_delayed", "refund_requested", "customer_message_received"):
        assert policy.classify(et).wake is True


def test_benign_events_are_logged_only():
    for et in ("payment_confirmed", "shipment_created"):
        assert policy.classify(et).wake is False


def test_delivered_wakes_for_wrap_up():
    assert policy.classify("delivered").wake is True


def test_unknown_event_escalates():
    assert policy.classify("something_weird").wake is True


def test_aggressive_mode_wakes_on_benign():
    assert policy.classify("payment_confirmed", {"mode": "aggressive"}).wake is True


def test_policy_override_of_log_only():
    # Move payment_confirmed into the wake set via config.
    wp = {"wake_events": ["payment_confirmed"], "log_only_events": []}
    assert policy.classify("payment_confirmed", wp).wake is True
