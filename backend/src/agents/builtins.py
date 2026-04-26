"""Builtin executor + evaluator agent definitions.

These two agents have secondary modes (plan_for_handoff, prepare_continue_to_work)
whose tool surfaces and briefings are too rich to express comfortably as YAML
frontmatter. They live as Python literals so the briefings can be plain strings
and the tool lists can be derived from named constants.

The legacy ``backend/config/agents/executor.md`` and ``evaluator.md`` were
removed when this module was introduced; user-defined agents continue to load
from the YAML directory via :mod:`agents.loader`.

See ``docs/architecture/agent-mode-system-v1.md``.
"""

from __future__ import annotations

from agents.briefings import (
    PLAN_FOR_HANDOFF_BRIEFING,
    PREPARE_CONTINUE_TO_WORK_BRIEFING,
)
from agents.types import AgentDefinition, ModeDefinition

# ---------------------------------------------------------------------------
# Tool surfaces
# ---------------------------------------------------------------------------

# Tools both agents may call directly to read the workspace and reason about
# the codebase. Keep this list small — it is the surface that secondary modes
# will allow alongside their terminal.
_READ_AND_SEARCH_TOOLS: list[str] = [
    "daytona_grep",
    "daytona_glob",
    "daytona_read_file",
    "daytona_shell",
    "ci_query_symbol",
    "ci_diagnostics",
    "ci_workspace_structure",
]

# Executor write/edit surface — only valid in the executor's direct mode.
_EXECUTOR_WRITE_TOOLS: list[str] = [
    "daytona_write_file",
    "daytona_edit_file",
    "daytona_delete_file",
    "daytona_move_file",
]

# Evaluator write surface — small fix-then-complete capability.
_EVALUATOR_WRITE_TOOLS: list[str] = [
    "daytona_write_file",
    "daytona_edit_file",
]


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------

_EXECUTOR_SYSTEM_PROMPT = """\
**Role**
You own one task in the executor-evaluator tree. Your job is to either complete \
the work directly or decompose it into a DAG plan that child executors can run.

**Rules to Follow**
You must read the playbook before acting. Your first assistant action is exactly \
one tool call: `load_skill(skill_name="executor-playbook")`. Do not batch that \
first load with any other tool call. Use the playbook to choose between direct \
completion and a plan handoff.

**Forbidden Actions**
Never edit test files or test suites to pass acceptance criteria. Never call \
`submit_continue_to_work` — that is evaluator-only.

**Task Completion**
End your turn with exactly one terminal tool call. In the default `direct` mode, \
that is `submit_task_completion` (when you can finish the work yourself). When \
the task needs decomposition, call `enter_plan_for_handoff` to switch into \
planning mode; from planning mode the only exit is `submit_plan_handoff`.
"""


EXECUTOR = AgentDefinition(
    name="executor",
    description=(
        "Owner of a task. Runs trivial work directly or hands off complex work "
        "via a DAG plan."
    ),
    role="executor",
    agent_type="agent",
    model="inherit",
    tool_call_limit=100,
    skills=["executor-playbook"],
    system_prompt=_EXECUTOR_SYSTEM_PROMPT,
    modes=[
        ModeDefinition(
            name="direct",
            is_default=True,
            allowed_tools=[
                *_READ_AND_SEARCH_TOOLS,
                *_EXECUTOR_WRITE_TOOLS,
                "enter_plan_for_handoff",
                "submit_task_completion",
            ],
            disallowed_tools=[
                "submit_plan_handoff",
                "submit_continue_to_work",
                "enter_prepare_continue_to_work",
            ],
            terminals=["submit_task_completion"],
        ),
        ModeDefinition(
            name="plan_for_handoff",
            allowed_tools=[
                *_READ_AND_SEARCH_TOOLS,
                "ask_user",
            ],
            terminals=["submit_plan_handoff"],
            entry_tool="enter_plan_for_handoff",
            briefing=PLAN_FOR_HANDOFF_BRIEFING,
        ),
    ],
)


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------

_EVALUATOR_SYSTEM_PROMPT = """\
**Role**
You are the closure gate for one handoff. After every sink task in the DAG \
passes, you read the acceptance criteria, the optional handoff note, and the \
child summaries, then decide whether the parent task can be claimed complete.

**Rules to Follow**
You must read the playbook before acting. Your first assistant action is exactly \
one tool call: `load_skill(skill_name="evaluator-playbook")`. Do not batch that \
first load with any other tool call. Use the playbook to choose between \
completion, trivial fix-then-complete, and continuation.

**Forbidden Actions**
Never edit test files or test suites to pass acceptance criteria. Never invoke \
the executor's handoff tools — those are executor-only.

**Task Completion**
End your turn with exactly one terminal tool call. In the default `direct` mode, \
that is `submit_task_completion` (criteria satisfied). When a gap remains, call \
`enter_prepare_continue_to_work` to switch into preparation mode; from \
preparation mode the only exit is `submit_continue_to_work`.
"""


EVALUATOR = AgentDefinition(
    name="evaluator",
    description=(
        "Closure gate for a handoff. Validates evidence, may fix trivial issues, "
        "decides task completion or continuation."
    ),
    role="evaluator",
    agent_type="agent",
    model="inherit",
    tool_call_limit=100,
    skills=["evaluator-playbook"],
    system_prompt=_EVALUATOR_SYSTEM_PROMPT,
    modes=[
        ModeDefinition(
            name="direct",
            is_default=True,
            allowed_tools=[
                *_READ_AND_SEARCH_TOOLS,
                *_EVALUATOR_WRITE_TOOLS,
                "enter_prepare_continue_to_work",
                "submit_task_completion",
            ],
            disallowed_tools=[
                "submit_plan_handoff",
                "enter_plan_for_handoff",
                "submit_continue_to_work",
            ],
            terminals=["submit_task_completion"],
        ),
        ModeDefinition(
            name="prepare_continue_to_work",
            allowed_tools=[
                *_READ_AND_SEARCH_TOOLS,
                "ask_user",
            ],
            terminals=["submit_continue_to_work"],
            entry_tool="enter_prepare_continue_to_work",
            briefing=PREPARE_CONTINUE_TO_WORK_BRIEFING,
        ),
    ],
)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


BUILTIN_AGENTS: tuple[AgentDefinition, ...] = (EXECUTOR, EVALUATOR)


def register_builtin_agents() -> None:
    """Register the executor and evaluator definitions in the global registry.

    Idempotent — safe to call from multiple bootstrap paths (server lifespan,
    test fixtures, CLI helpers).
    """
    from agents.registry import register_definition

    for defn in BUILTIN_AGENTS:
        register_definition(defn)


__all__ = [
    "BUILTIN_AGENTS",
    "EVALUATOR",
    "EXECUTOR",
    "register_builtin_agents",
]
