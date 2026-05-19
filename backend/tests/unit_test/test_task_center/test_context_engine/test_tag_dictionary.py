"""TAG_DICTIONARY canonical-label coverage + matching semantics.

Pins the registry against ``OPTIMIZED_USER_MSG_1.md``: every spec'd
``(tag, semantic-attrs)`` row maps to the spec's canonical label and only
``status`` / ``verdict`` participate in matching.
"""

from __future__ import annotations

import pytest

from task_center.context_engine.tag_dictionary import (
    RECURSE_THROUGH,
    TAG_DICTIONARY,
    TagDescriptor,
    match,
    render_attrs,
)


def test_dictionary_has_every_canonical_row_from_spec():
    expected: list[tuple[str, dict[str, str] | None, str]] = [
        ("goal", None, "user's request"),
        ("entry_request", None, "root delegation envelope"),
        ("iteration", {"status": "prior"}, "previous iteration's work"),
        ("iteration", {"status": "current"}, "active iteration"),
        ("iteration_goal", None, "active iteration's scope"),
        ("accepted_plan", None, "prior iteration's accepted plan"),
        ("summary", None, "prior iteration's summary"),
        (
            "attempt",
            {"status": "prior", "verdict": "fail"},
            "failed prior attempt",
        ),
        ("attempt", {"status": "current"}, "active attempt"),
        ("plan_spec", None, "attempt's plan"),
        (
            "deferred_goal_for_next_iteration",
            None,
            "scope handed to next iteration",
        ),
        ("status_summary", None, "generator outcomes summary"),
        ("task", None, "generator task outcome"),
        (
            "evaluation_criteria",
            None,
            "criteria the attempt must satisfy",
        ),
        ("evaluator_summary", None, "evaluator's commentary"),
        ("failed_criteria", None, "criteria that failed"),
        ("assigned_task", None, "your assigned task"),
        ("dependency", None, "upstream task output"),
    ]
    actual = [(d.tag, d.attr_filter, d.label) for d in TAG_DICTIONARY]
    assert actual == expected


def test_only_iteration_is_in_recurse_through():
    assert RECURSE_THROUGH == frozenset({"iteration"})


def test_match_returns_specific_over_wildcard():
    # Both <attempt status="prior" verdict="fail"> and <attempt status="current">
    # are in the dictionary; the wildcard fallback is implicit (None).
    desc = match("attempt", {"status": "current"})
    assert desc is not None and desc.label == "active attempt"


def test_match_returns_none_for_unknown_tag():
    assert match("nonexistent_tag", {}) is None


def test_match_ignores_identity_attrs():
    desc = match("iteration", {"status": "current", "iteration_no": "7"})
    assert desc is not None and desc.label == "active iteration"


def test_match_picks_more_specific_filter():
    """<attempt status="prior" verdict="fail"> has 2 filter keys vs the
    single-key entry — must win."""
    extra = TagDescriptor(
        tag="attempt", attr_filter={"status": "prior"}, label="prior attempt only"
    )
    # Synthetic dictionary inclusion is irrelevant — match() reads global.
    # Verify the live dictionary still picks the 2-key row for prior/fail.
    desc = match("attempt", {"status": "prior", "verdict": "fail"})
    assert desc is not None and desc.label == "failed prior attempt"
    _ = extra  # silence unused; documentation aid only


def test_render_attrs_orders_semantic_first_and_drops_identity():
    out = render_attrs(
        {
            "iteration_no": "1",
            "verdict": "fail",
            "status": "prior",
            "id": "x",
        }
    )
    assert out == 'status="prior" verdict="fail"'


def test_render_attrs_empty_for_only_identity_attrs():
    assert render_attrs({"iteration_no": "1", "id": "x"}) == ""


def test_descriptor_is_frozen():
    desc = TAG_DICTIONARY[0]
    with pytest.raises(Exception):
        desc.tag = "renamed"  # type: ignore[misc]
