"""``planner`` recipe — context for one attempt planner spawn.

Two history paths, one rendering:

* **relay** (feed-forward): prior iterations' canonical reducer outcomes
  (``iteration.outcomes``), rendered as ``<iteration position="prior">`` groups
  of ``<task>`` children.
* **retry** (feedback): the current iteration's failed attempts, each an
  ``<attempt attempt_no="k">`` group of the failed plan tasks' ``<task>``s plus
  a ``<failure>`` line (the generalized ``attempt_failure_line``). When a
  generator fails before any reducer runs, this is the only feedback the next
  planner gets.

The frame is ``<workflow_goal>`` (via ``<goal>``) + the current
``<iteration_goal>``. The ``<Task Guidance>`` row is assembled at launch time by
``AgentEntryComposer``; recipes emit only context blocks.
"""

from __future__ import annotations

from task_center._core.outcomes import (
    attempt_failure_line,
    generator_outcomes,
    parse_outcomes_record,
    reducer_outcomes,
)
from task_center._core.state import Attempt, AttemptStatus, Iteration, Workflow
from task_center.context_engine.engine import ContextEngineDeps, ContextEngineError
from task_center.context_engine.packet import (
    ContextBlock,
    ContextBlockKind,
    ContextPacket,
    ContextPriority,
    ContextRefs,
)
from task_center.context_engine.recipes._task_xml import (
    block_task_body,
    render_task_element,
    sanitize_fragment,
    task_attrs,
)
from task_center.context_engine.recipes_registry import ContextRecipe
from task_center.context_engine.scope import ContextScope

PLANNER_ID = "planner"
_REQUIRED_FIELDS = frozenset({"workflow_id", "iteration_id", "attempt_id"})

# For iteration 1 the iteration goal equals the user's request. Echoing the full
# text twice is noise; the planner skill knows what this marker means.
_ITERATION_GOAL_IDENTITY_BODY = "(identical to &lt;goal&gt;)"


def build_planner_context(scope: ContextScope, deps: ContextEngineDeps) -> ContextPacket:
    workflow_id = scope.require_field("workflow_id")
    iteration_id = scope.require_field("iteration_id")
    attempt_id = scope.require_field("attempt_id")

    workflow = deps.workflow_store.get(workflow_id)
    if workflow is None:
        raise ContextEngineError(f"Workflow {workflow_id!r} not found")
    iteration = deps.iteration_store.get(iteration_id)
    if iteration is None:
        raise ContextEngineError(f"Iteration {iteration_id!r} not found")

    blocks = _goal_iteration_blocks(
        workflow=workflow,
        current_iteration=iteration,
        iterations=deps.iteration_store.list_for_workflow(workflow.id),
    )
    blocks.extend(
        _failed_attempt_blocks(
            current_attempt_id=attempt_id,
            iteration=iteration,
            attempts=deps.attempt_store.list_for_iteration(iteration.id),
            task_store=deps.task_store,
        )
    )

    return ContextPacket(
        target_role="planner",
        target_id=attempt_id,
        canonical_refs=ContextRefs(
            workflow_id=workflow.id,
            iteration_id=iteration.id,
            attempt_id=attempt_id,
        ),
        blocks=blocks,
        source_ids=[b.source_id for b in blocks if b.source_id],
    )


# ---- goal / iteration frame (relay) ---------------------------------------


def _current_iteration_group_id(iteration: Iteration) -> str:
    return f"iteration_{iteration.sequence_no}_current"


def _current_iteration_group_attrs(iteration: Iteration) -> str:
    return f'iteration_no="{iteration.sequence_no}" position="current"'


def _goal_iteration_blocks(
    *,
    workflow: Workflow,
    current_iteration: Iteration,
    iterations: list[Iteration],
) -> list[ContextBlock]:
    blocks: list[ContextBlock] = [_goal_statement_block(workflow)]
    blocks.extend(_prior_iteration_blocks(current=current_iteration, iterations=iterations))
    blocks.append(_current_iteration_goal_child(current_iteration))
    return blocks


def _goal_statement_block(workflow: Workflow) -> ContextBlock:
    return ContextBlock(
        kind=ContextBlockKind.GOAL_STATEMENT,
        priority=ContextPriority.REQUIRED,
        text=workflow.workflow_goal,
        source_id=workflow.id,
        source_kind="goal",
        metadata={"tag": "goal"},
    )


def _current_iteration_goal_child(iteration: Iteration) -> ContextBlock:
    body = (
        _ITERATION_GOAL_IDENTITY_BODY
        if iteration.sequence_no == 1
        else iteration.iteration_goal
    )
    return ContextBlock(
        kind=ContextBlockKind.ITERATION_STATEMENT,
        priority=ContextPriority.REQUIRED,
        text=body,
        source_id=iteration.id,
        source_kind="iteration",
        metadata={
            "group_id": _current_iteration_group_id(iteration),
            "group_tag": "iteration",
            "group_attrs": _current_iteration_group_attrs(iteration),
            "child_tag": "iteration_goal",
            "iteration_no": str(iteration.sequence_no),
        },
    )


def _prior_iteration_blocks(
    *,
    current: Iteration,
    iterations: list[Iteration],
) -> list[ContextBlock]:
    """Emit ``<iteration position="prior">`` groups from each prior iteration's outcomes."""
    priors = sorted(
        (s for s in iterations if s.sequence_no < current.sequence_no),
        key=lambda s: s.sequence_no,
    )
    out: list[ContextBlock] = []
    immediate_prior = current.sequence_no - 1
    for prior in priors:
        priority = (
            ContextPriority.HIGH if prior.sequence_no == immediate_prior else ContextPriority.MEDIUM
        )
        group_id = f"iteration_{prior.sequence_no}_prior"
        group_attrs = f'iteration_no="{prior.sequence_no}" position="prior"'
        for outcome in parse_outcomes_record(prior.outcomes):
            text, pre_rendered = block_task_body(outcome)
            metadata = {
                "group_id": group_id,
                "group_tag": "iteration",
                "group_attrs": group_attrs,
                "child_tag": "task",
                "attrs": task_attrs(outcome),
            }
            if pre_rendered:
                metadata["pre_rendered_xml"] = "true"
            out.append(
                ContextBlock(
                    kind=ContextBlockKind.PRIOR_ITERATION_SUMMARY,
                    priority=priority,
                    text=text,
                    source_id=prior.id,
                    source_kind="iteration",
                    metadata=metadata,
                )
            )
    return out


# ---- failed attempts (retry) ----------------------------------------------


def _failed_attempt_blocks(
    *,
    current_attempt_id: str | None,
    iteration: Iteration,
    attempts: list[Attempt],
    task_store,
) -> list[ContextBlock]:
    """Return one ``<attempt attempt_no="k">`` block per failed prior attempt."""
    failed = sorted(
        (t for t in attempts if t.status == AttemptStatus.FAILED and t.id != current_attempt_id),
        key=lambda t: t.attempt_sequence_no,
    )
    group_id = _current_iteration_group_id(iteration)
    group_attrs = _current_iteration_group_attrs(iteration)
    return [
        ContextBlock(
            kind=ContextBlockKind.FAILED_ATTEMPT,
            priority=ContextPriority.HIGH,
            text=_render_failed_attempt_body(t, task_store=task_store),
            source_id=t.id,
            source_kind="attempt",
            metadata={
                "group_id": group_id,
                "group_tag": "iteration",
                "group_attrs": group_attrs,
                "child_tag": "attempt",
                "attrs": f'attempt_no="{t.attempt_sequence_no}"',
                "pre_rendered_xml": "true",
            },
        )
        for t in failed
    ]


def _render_failed_attempt_body(attempt: Attempt, *, task_store) -> str:
    """Render the inside of ``<attempt attempt_no="k">…</attempt>``.

    Terminal plan-task ``<task>``s (generators + reducers; un-started excluded)
    followed by a ``<failure>`` line. There is no ``<evaluator_summary>`` — the
    retry feedback is the failed *tasks* plus the fail reason.
    """
    parts = [
        render_task_element(outcome, source_id=attempt.id)
        for outcome in generator_outcomes(attempt, task_store=task_store)
        + reducer_outcomes(attempt, task_store=task_store)
        if outcome.is_terminal
    ]
    failure = sanitize_fragment(attempt_failure_line(attempt, task_store), attempt.id)
    parts.append(f"<failure>\n{failure}\n</failure>")
    return "\n".join(parts)


PLANNER_RECIPE = ContextRecipe(
    id=PLANNER_ID,
    required_scope_fields=_REQUIRED_FIELDS,
    build=build_planner_context,
)
