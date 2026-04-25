"""Unit tests for ``task_center.phases.compile_phases``."""

from __future__ import annotations

import pytest

from task_center import PhaseValidationError
from task_center.phases import compile_phases


def test_rejects_bare_strings() -> None:
    with pytest.raises(PhaseValidationError, match="entries must be objects"):
        compile_phases([["A"]], {"A": {"title": "A", "spec": "..."}})


def test_rejects_unknown_id() -> None:
    with pytest.raises(PhaseValidationError, match="not a key in task_specs"):
        compile_phases([[{"id": "X"}]], {"A": {"title": "A", "spec": "..."}})


def test_rejects_duplicate_id_across_phases() -> None:
    with pytest.raises(PhaseValidationError, match="duplicate task id"):
        compile_phases(
            [[{"id": "A"}], [{"id": "A", "needs": ["A"]}]],
            {"A": {"title": "A", "spec": "..."}},
        )


def test_rejects_empty_phases_list() -> None:
    with pytest.raises(PhaseValidationError, match="phases must be a non-empty list"):
        compile_phases([], {"A": {"title": "A", "spec": "..."}})


def test_rejects_empty_task_specs() -> None:
    with pytest.raises(PhaseValidationError, match="task_specs must be a non-empty dict"):
        compile_phases([[{"id": "A"}]], {})


def test_rejects_inner_empty_phase() -> None:
    with pytest.raises(PhaseValidationError, match="must be a non-empty list of entries"):
        compile_phases([[{"id": "A"}], []], {"A": {"title": "A", "spec": "..."}})


def test_rejects_needs_on_phase_one() -> None:
    with pytest.raises(PhaseValidationError, match="'needs' is not allowed on phase 1"):
        compile_phases(
            [[{"id": "A", "needs": []}]],
            {"A": {"title": "A", "spec": "..."}},
        )


def test_rejects_needs_pointing_forward() -> None:
    with pytest.raises(PhaseValidationError, match="must be strictly earlier"):
        compile_phases(
            [
                [{"id": "A"}],
                [{"id": "B", "needs": ["C"]}],
                [{"id": "C"}],
            ],
            {tid: {"title": tid, "spec": "..."} for tid in ("A", "B", "C")},
        )


def test_rejects_needs_pointing_same_phase() -> None:
    with pytest.raises(PhaseValidationError, match="must be strictly earlier"):
        compile_phases(
            [
                [{"id": "A"}],
                [{"id": "B", "needs": ["C"]}, {"id": "C"}],
            ],
            {tid: {"title": tid, "spec": "..."} for tid in ("A", "B", "C")},
        )


def test_rejects_self_reference_in_needs() -> None:
    with pytest.raises(PhaseValidationError, match="entry's own id"):
        compile_phases(
            [[{"id": "A"}], [{"id": "B", "needs": ["B"]}]],
            {tid: {"title": tid, "spec": "..."} for tid in ("A", "B")},
        )


def test_rejects_duplicates_in_needs() -> None:
    with pytest.raises(PhaseValidationError, match="duplicate ids"):
        compile_phases(
            [[{"id": "A"}], [{"id": "B", "needs": ["A", "A"]}]],
            {tid: {"title": tid, "spec": "..."} for tid in ("A", "B")},
        )


def test_rejects_dangling_needs_reference() -> None:
    with pytest.raises(PhaseValidationError, match="references unknown id"):
        compile_phases(
            [[{"id": "A"}], [{"id": "B", "needs": ["GHOST"]}]],
            {tid: {"title": tid, "spec": "..."} for tid in ("A", "B")},
        )


def test_doc_example_compiles() -> None:
    phases = [
        [{"id": "A"}, {"id": "B"}, {"id": "C"}],
        [{"id": "D", "needs": ["A"]}, {"id": "E", "needs": ["B"]}],
        [{"id": "F"}],
    ]
    task_specs = {tid: {"title": tid, "spec": "..."} for tid in ("A", "B", "C", "D", "E", "F")}
    deps = compile_phases(phases, task_specs)
    assert deps["A"] == frozenset()
    assert deps["B"] == frozenset()
    assert deps["C"] == frozenset()
    assert deps["D"] == frozenset({"A"})
    assert deps["E"] == frozenset({"B"})
    assert deps["F"] == frozenset({"D", "E"})


def test_skip_back_edge_to_phase_one() -> None:
    phases = [
        [{"id": "A"}, {"id": "B"}],
        [{"id": "C"}],
        [{"id": "D", "needs": ["A"]}],
    ]
    task_specs = {tid: {"title": tid, "spec": "..."} for tid in ("A", "B", "C", "D")}
    deps = compile_phases(phases, task_specs)
    assert deps["C"] == frozenset({"A", "B"})
    assert deps["D"] == frozenset({"A"})


def test_explicit_empty_needs_means_no_deps() -> None:
    deps = compile_phases(
        [[{"id": "A"}], [{"id": "B", "needs": []}]],
        {tid: {"title": tid, "spec": "..."} for tid in ("A", "B")},
    )
    assert deps["B"] == frozenset()


def test_omitted_needs_on_phase_two_uses_implicit_default() -> None:
    deps = compile_phases(
        [[{"id": "A"}, {"id": "B"}], [{"id": "C"}]],
        {tid: {"title": tid, "spec": "..."} for tid in ("A", "B", "C")},
    )
    assert deps["C"] == frozenset({"A", "B"})


def test_needs_must_be_list() -> None:
    with pytest.raises(PhaseValidationError, match="'needs' must be a list"):
        compile_phases(
            [[{"id": "A"}], [{"id": "B", "needs": "A"}]],
            {tid: {"title": tid, "spec": "..."} for tid in ("A", "B")},
        )
