"""Branch matrix + registration-safety tests for role_instruction helpers."""

from __future__ import annotations

from task_center.context_engine.packet import (
    ContextBlockKind,
    ContextPriority,
)
from task_center.context_engine.recipes import role_instruction
from task_center.context_engine.recipes.role_instruction import (
    _ADVISOR_DEFAULT,
    _ADVISOR_INSTRUCTIONS,
    advisor_instruction,
    evaluator_instruction,
    generator_instruction,
    planner_instruction,
    resolver_instruction,
)
from task_center.context_engine.recipes_registry import ContextRecipe


# Tool names that must never appear in any hint text (profile-variant
# agnosticism: Generator has four .md variants and Planner has two; hint text
# describes situations and decisions, not terminal submission tools).
_BANNED_TERMINAL_TOOL_NAMES = (
    "submit_evaluation_success",
    "submit_evaluation_failure",
    "submit_plan_closes_goal",
    "submit_plan_continues_goal",
    "submit_full_plan",
    "submit_partial_plan",
)


def _assert_role_instruction_shape(block) -> None:
    assert block.kind == ContextBlockKind.ROLE_INSTRUCTION.value
    assert block.priority == ContextPriority.REQUIRED
    assert len(block.text.strip()) > 0
    for banned in _BANNED_TERMINAL_TOOL_NAMES:
        assert banned not in block.text, (
            f"Hint text leaked terminal tool name {banned!r}: {block.text!r}"
        )


# ---------------------------------------------------------------------------
# Planner branch matrix
# ---------------------------------------------------------------------------


def test_planner_iter1_no_failed_attempts():
    block = planner_instruction(
        iteration_sequence_no=1, has_failed_attempts=False
    )
    _assert_role_instruction_shape(block)
    assert "first attempt" in block.text
    assert "continuation_goal" in block.text
    assert "list of independent items" in block.text
    assert "one criterion per item" in block.text
    assert "next bounded slice" in block.text
    assert "entire remaining backlog" in block.text


def test_planner_iter1_with_failed_attempts():
    block = planner_instruction(
        iteration_sequence_no=1, has_failed_attempts=True
    )
    _assert_role_instruction_shape(block)
    assert "prior attempts in this iteration failed" in block.text
    assert "meaningfully different" in block.text
    assert "list of independent items" in block.text
    assert "one criterion per item" in block.text


def test_planner_iter_n_no_failed_attempts():
    block = planner_instruction(
        iteration_sequence_no=3, has_failed_attempts=False
    )
    _assert_role_instruction_shape(block)
    assert "Previous Iteration Results" in block.text
    assert "continue from where the prior iteration ended" in block.text
    assert "Current Iteration text is the authoritative scope" in block.text
    assert "do not add backlog items" in block.text
    assert "list of independent items" in block.text
    assert "one criterion per item" in block.text


def test_planner_iter_n_with_failed_attempts():
    block = planner_instruction(
        iteration_sequence_no=2, has_failed_attempts=True
    )
    _assert_role_instruction_shape(block)
    assert "Previous Iteration Results" in block.text
    assert "Prior Failed Attempts" in block.text
    assert "Current Iteration text is the authoritative scope" in block.text
    assert "do not add backlog items" in block.text
    assert "list of independent items" in block.text
    assert "one criterion per item" in block.text


# ---------------------------------------------------------------------------
# Generator branch matrix
# ---------------------------------------------------------------------------


def test_generator_no_deps():
    block = generator_instruction(has_deps=False)
    _assert_role_instruction_shape(block)
    assert "no dependencies" in block.text
    assert "produce the deliverable" in block.text


def test_generator_with_deps():
    block = generator_instruction(has_deps=True)
    _assert_role_instruction_shape(block)
    assert "Dependency Results" in block.text
    assert "fixed inputs" in block.text


# ---------------------------------------------------------------------------
# Evaluator branch matrix
# ---------------------------------------------------------------------------


def test_evaluator_full_plan():
    block = evaluator_instruction(is_partial=False)
    _assert_role_instruction_shape(block)
    assert "Evaluation Criteria" in block.text
    assert "pass/fail" in block.text


def test_evaluator_partial_plan():
    block = evaluator_instruction(is_partial=True)
    _assert_role_instruction_shape(block)
    assert "Partial Plan Boundary" in block.text
    assert (
        "do not penalize for incomplete work that was explicitly deferred"
        in block.text
    )


# ---------------------------------------------------------------------------
# Registration safety — role_instruction.py must not be auto-discovered as a
# recipe. recipes/__init__.py walks every submodule and registers any
# attribute whose name endswith '_RECIPE' and is a ContextRecipe instance.
# ---------------------------------------------------------------------------


def test_module_exposes_no_recipe_symbols():
    for attr_name in dir(role_instruction):
        if attr_name.startswith("_"):
            continue
        assert not attr_name.endswith("_RECIPE"), (
            f"role_instruction must not expose a *_RECIPE symbol; found "
            f"{attr_name!r}. Auto-discovery in recipes/__init__.py would "
            "register it as a recipe."
        )
        value = getattr(role_instruction, attr_name)
        assert not isinstance(value, ContextRecipe), (
            f"role_instruction must not expose a ContextRecipe instance; "
            f"found {attr_name!r}."
        )


# ---------------------------------------------------------------------------
# Advisor dispatch — per-terminal instructions + default fallback.
# (Advisor is profile-variant-bound to one .md, so unlike planner/generator/
# evaluator the per-terminal coupling is allowed and expected.)
# ---------------------------------------------------------------------------


_EXPECTED_ADVISOR_TERMINALS = frozenset(
    {
        "submit_plan_closes_goal",
        "submit_plan_continues_goal",
        "submit_evaluation_success",
        "submit_evaluation_failure",
        "submit_execution_success",
        "submit_execution_failure",
        "submit_execution_handoff",
        "submit_verification_success",
        "submit_verification_failure",
        "submit_exploration_result",
    }
)


def _assert_advisor_block_shape(block) -> None:
    assert block.kind == ContextBlockKind.ROLE_INSTRUCTION.value
    assert block.priority == ContextPriority.REQUIRED
    assert len(block.text.strip()) > 0


def test_advisor_instruction_covers_all_known_terminals():
    assert set(_ADVISOR_INSTRUCTIONS.keys()) == _EXPECTED_ADVISOR_TERMINALS
    texts = list(_ADVISOR_INSTRUCTIONS.values())
    # Each entry is distinct so the advisor's instructions are not
    # boilerplate copy-paste.
    assert len(set(texts)) == len(texts)
    for tool_name in _ADVISOR_INSTRUCTIONS:
        block = advisor_instruction(tool_name=tool_name)
        _assert_advisor_block_shape(block)
        assert block.text == _ADVISOR_INSTRUCTIONS[tool_name]


def test_advisor_instruction_falls_back_to_default():
    block = advisor_instruction(tool_name="never_seen_terminal_name")
    _assert_advisor_block_shape(block)
    assert block.text == _ADVISOR_DEFAULT


def test_advisor_dispatch_keys_match_submission_filenames():
    """Every advisor dispatch key matches a backend/src/tools/submission/submit_*.py file."""
    import pathlib

    repo_root = pathlib.Path(__file__).resolve().parents[5]
    submission_dir = repo_root / "backend" / "src" / "tools" / "submission"
    on_disk = {
        path.stem
        for path in submission_dir.rglob("submit_*.py")
        if path.is_file()
    }
    assert _EXPECTED_ADVISOR_TERMINALS <= on_disk, (
        f"Advisor dispatch keys missing on disk: "
        f"{_EXPECTED_ADVISOR_TERMINALS - on_disk}"
    )


def test_resolver_instruction_mentions_parent_transcript():
    block = resolver_instruction()
    _assert_advisor_block_shape(block)
    assert "parent transcript" in block.text.lower()


# Carve-outs: helper self-terminals (advisor/resolver) don't self-advise, so
# they are not expected to appear as _ADVISOR_INSTRUCTIONS keys.
_ADVISOR_DISPATCH_CARVE_OUTS = frozenset(
    {"submit_advisor_feedback", "submit_resolver_result"}
)


def test_every_submission_terminal_has_advisor_dispatch_entry():
    """Reverse-direction guard for the Phase 1 dispatch test.

    A new `submit_*.py` terminal landing without an `_ADVISOR_INSTRUCTIONS`
    entry would silently fall back to `_ADVISOR_DEFAULT`. This test fails
    loudly on that case so the contributor adds an entry (or documents the
    fallback intent by extending the carve-out set).
    """
    import pathlib

    repo_root = pathlib.Path(__file__).resolve().parents[5]
    submission_dir = repo_root / "backend" / "src" / "tools" / "submission"
    on_disk = {
        path.stem
        for path in submission_dir.rglob("submit_*.py")
        if path.is_file()
    }
    expected_dispatch_keys = on_disk - _ADVISOR_DISPATCH_CARVE_OUTS
    missing = expected_dispatch_keys - set(_ADVISOR_INSTRUCTIONS.keys())
    assert not missing, (
        f"submit_*.py terminals missing from _ADVISOR_INSTRUCTIONS dispatch: "
        f"{sorted(missing)}. Add an entry to recipes/role_instruction.py or "
        f"extend _ADVISOR_DISPATCH_CARVE_OUTS in this test if the fallback "
        f"to _ADVISOR_DEFAULT is intentional."
    )
