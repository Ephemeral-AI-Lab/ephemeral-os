"""Branch matrix + registration-safety tests for role_instruction helpers."""

from __future__ import annotations

from task_center.context_engine.packet import (
    ContextBlockKind,
    ContextPriority,
)
from task_center.context_engine.recipes import role_instruction
from task_center.context_engine.recipes.role_instruction import (
    evaluator_instruction,
    generator_instruction,
    planner_instruction,
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
    assert "list of independent items" in block.text
    assert "one criterion per item" in block.text


def test_planner_iter_n_with_failed_attempts():
    block = planner_instruction(
        iteration_sequence_no=2, has_failed_attempts=True
    )
    _assert_role_instruction_shape(block)
    assert "Previous Iteration Results" in block.text
    assert "Prior Failed Attempts" in block.text
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
