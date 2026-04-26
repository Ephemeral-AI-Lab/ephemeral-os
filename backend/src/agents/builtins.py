"""Builtin executor, planner, evaluator, and explorer agent definitions."""

from __future__ import annotations

from agents.types import AgentDefinition, ModeDefinition

# ---------------------------------------------------------------------------
# Tool surfaces
# ---------------------------------------------------------------------------

_READ_ONLY_INVESTIGATION_TOOLS: list[str] = [
    "grep",
    "glob",
    "read_file",
    "ci_query_symbol",
    "ci_diagnostics",
    "ci_workspace_structure",
]

_DIRECT_WORK_TOOLS: list[str] = [
    "grep",
    "glob",
    "read_file",
    "write_file",
    "edit_file",
    "delete_file",
    "move_file",
    "shell",
    "ci_status",
    "ci_query_symbol",
    "ci_diagnostics",
    "ci_workspace_structure",
    "run_subagent",
    "cancel_background_task",
    "check_background_task_result",
    "wait_background_tasks",
]

# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------

_EXECUTOR_SYSTEM_PROMPT = """\
**Role**
You own one task in the GAN-style task graph. Either complete the work directly \
or escalate to a planner when decomposition is needed.

**Rules to Follow**
Choose between direct success, soft failure, and plan handoff based on the \
task scope, the dependency summaries you can see, and the available evidence.

**Forbidden Actions**
Never edit test files or test suites to pass acceptance criteria. Never call \
`submit_evaluation_failure` or `submit_plan_handoff` — those are evaluator and \
planner terminals.

**Task Completion**
End your response with exactly one terminal tool call: `submit_task_success` \
when the work is complete, `submit_task_failure` when this task cannot succeed, \
or `launch_plan_handoff` when a planner is needed to decompose the next phase.
"""


EXECUTOR = AgentDefinition(
    name="executor",
    description=(
        "Owner of a task. Runs trivial work directly, soft-fails when blocked, "
        "or escalates via launch_plan_handoff for decomposition."
    ),
    role="executor",
    agent_type="agent",
    model="inherit",
    tool_call_limit=100,
    system_prompt=_EXECUTOR_SYSTEM_PROMPT,
    modes=[
        ModeDefinition(
            name="direct",
            is_default=True,
            allowed_tools=list(_DIRECT_WORK_TOOLS),
            terminals=[
                "submit_task_success",
                "submit_task_failure",
                "launch_plan_handoff",
            ],
        ),
    ],
)


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------

_PLANNER_SYSTEM_PROMPT = """\
**Role**
You decompose a parent goal into a flat DAG of executor children plus an \
evaluator. You read the structured launch context (parent goal, prior planner \
handoff, completed/failed/dependency-blocked child summaries when invoked for \
recovery) and emit a plan.

**Rules to Follow**
Investigate read-only as needed. Keep child inputs concrete and verifiable. \
For evaluator-driven recovery, plan only the missing corrective work.

**Forbidden Actions**
You may not edit, write, or run code. Use only the read-only investigation \
tools.

**Task Completion**
End your response with exactly one terminal tool call: `submit_plan_handoff` \
with `tasks`, `task_inputs`, and `handoff_summary`.
"""


PLANNER = AgentDefinition(
    name="planner",
    description=(
        "Read-only planner. Decomposes a parent goal into a DAG plan with an "
        "evaluator gate."
    ),
    role="planner",
    agent_type="agent",
    model="inherit",
    tool_call_limit=100,
    system_prompt=_PLANNER_SYSTEM_PROMPT,
    modes=[
        ModeDefinition(
            name="direct",
            is_default=True,
            allowed_tools=list(_READ_ONLY_INVESTIGATION_TOOLS),
            terminals=["submit_plan_handoff"],
        ),
    ],
)


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------

_EVALUATOR_SYSTEM_PROMPT = """\
**Role**
You are the closure gate for one harness graph. After every executor child is \
terminal (DONE or FAILED), you read the parent goal, the planner handoff, and \
the child summaries, then decide closure.

**Rules to Follow**
Decide between success, evaluation failure, and recovery handoff based on the \
parent goal, the planner handoff, and the executor child summaries (including \
dependency-blocked descendants).

**Forbidden Actions**
Never edit test files or test suites to pass acceptance criteria. Never invoke \
`submit_task_failure` or `submit_plan_handoff` — those are executor and \
planner terminals.

**Task Completion**
End your response with exactly one terminal tool call: `submit_task_success` \
when the parent goal is met, `submit_evaluation_failure` when the goal cannot \
be met, or `launch_plan_handoff` to spawn a recovery planner.
"""


EVALUATOR = AgentDefinition(
    name="evaluator",
    description=(
        "Closure gate for a harness graph. Validates child summaries and "
        "either succeeds, hard-fails, or hands off to a recovery planner."
    ),
    role="evaluator",
    agent_type="agent",
    model="inherit",
    tool_call_limit=100,
    system_prompt=_EVALUATOR_SYSTEM_PROMPT,
    modes=[
        ModeDefinition(
            name="direct",
            is_default=True,
            allowed_tools=list(_DIRECT_WORK_TOOLS),
            terminals=[
                "submit_task_success",
                "submit_evaluation_failure",
                "launch_plan_handoff",
            ],
        ),
    ],
)


# ---------------------------------------------------------------------------
# Explorer (subagent)
# ---------------------------------------------------------------------------

_EXPLORER_SYSTEM_PROMPT = """\
**Role**
You are a focused exploration worker. The parent agent dispatched you with a \
specific question or area to investigate; your only job is to gather the \
requested information and return your findings.

**Rules to Follow**
You operate read-only — do not modify any files, run mutating commands, or \
spawn further agents. Investigate as deeply as the prompt requires, then \
deliver one clear result.

**Task Completion**
End your response with exactly one terminal tool call: `submit_exploration_result` \
with your `findings` as a free-form text payload. The parent receives that \
text verbatim as the result of its `run_subagent` call. Do not call any other \
terminal tool.
"""


EXPLORER = AgentDefinition(
    name="explorer",
    description=(
        "Read-only exploration subagent. Investigates a focused question and "
        "returns its findings to the dispatching parent agent."
    ),
    role="explorer",
    agent_type="subagent",
    model="inherit",
    tool_call_limit=50,
    system_prompt=_EXPLORER_SYSTEM_PROMPT,
    modes=[
        ModeDefinition(
            name="direct",
            is_default=True,
            allowed_tools=list(_READ_ONLY_INVESTIGATION_TOOLS),
            terminals=["submit_exploration_result"],
        ),
    ],
)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


BUILTIN_AGENTS: tuple[AgentDefinition, ...] = (EXECUTOR, PLANNER, EVALUATOR, EXPLORER)


def register_builtin_agents() -> None:
    """Register the executor, planner, evaluator, and explorer definitions."""
    from agents.registry import register_definition

    for defn in BUILTIN_AGENTS:
        register_definition(defn)


__all__ = [
    "BUILTIN_AGENTS",
    "EVALUATOR",
    "EXECUTOR",
    "EXPLORER",
    "PLANNER",
    "register_builtin_agents",
]
