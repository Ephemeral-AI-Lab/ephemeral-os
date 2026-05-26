"""Phase 2 layer_stack emitter tests.

Exercises the ``emit_squash_event`` helper and verifies events land on the
correct lane with the causal-chain identifiers populated.
"""

from __future__ import annotations

from sandbox.daemon.audit_buffer import reset_audit_buffer_for_tests
from sandbox.daemon.layer_stack_runtime import emit_squash_event


def _by_type(events: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {str(ev.get("type")): ev for ev in events}


def test_squash_completed_event_lands_on_critical_lane() -> None:
    buffer = reset_audit_buffer_for_tests()
    emit_squash_event(
        completed=True,
        input_layers=12,
        result_layers=3,
        manifest_root_hash_value="abcd",
    )
    response = buffer.pull(after_seq=-1, limit=100)
    typed = _by_type(response["events"])
    assert "layer_stack.squash_completed" in typed
    payload = typed["layer_stack.squash_completed"]["payload"]
    assert payload["layer_stack"]["squash_result_layers"] == 3
    assert payload["layer_stack"]["manifest_root_hash"] == "abcd"
    assert typed["layer_stack.squash_completed"]["lane"] == "critical"


def test_squash_failed_event_carries_failure_kind() -> None:
    buffer = reset_audit_buffer_for_tests()
    emit_squash_event(failed=True, failure_kind="plan_aborted")
    response = buffer.pull(after_seq=-1, limit=100)
    typed = _by_type(response["events"])
    assert "layer_stack.squash_failed" in typed
    assert (
        typed["layer_stack.squash_failed"]["payload"]["layer_stack"][
            "squash_failure_kind"
        ]
        == "plan_aborted"
    )


def test_squash_triggered_event_carries_reason() -> None:
    buffer = reset_audit_buffer_for_tests()
    emit_squash_event(
        triggered=True, trigger_reason="depth_cap", input_layers=64
    )
    response = buffer.pull(after_seq=-1, limit=100)
    typed = _by_type(response["events"])
    assert "layer_stack.squash_triggered" in typed
    payload = typed["layer_stack.squash_triggered"]["payload"]["layer_stack"]
    assert payload["squash_trigger_reason"] == "depth_cap"
    assert payload["squash_input_layers"] == 64
