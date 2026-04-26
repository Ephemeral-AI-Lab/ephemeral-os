"""Builtin executor + evaluator agent definitions.

These two agents have secondary modes (plan_for_handoff, prepare_continue_to_work)
whose tool surfaces and briefings are too rich to express comfortably as YAML
frontmatter. They live as Python literals so the tool lists can be derived from
named constants.

The legacy ``backend/config/agents/executor.md`` and ``evaluator.md`` were
removed when this module was introduced; user-defined agents continue to load
from the YAML directory via :mod:`agents.loader`.

See ``docs/architecture/agent-mode-system-v1.md``.
"""

from __future__ import annotations

from agents.types import AgentDefinition, ModeDefinition

# ---------------------------------------------------------------------------
# Tool surfaces
# ---------------------------------------------------------------------------

# Read-only tools available inside the secondary modes. The list intentionally
# excludes ``daytona_shell`` (which can run arbitrary writes) and any
# subagent-spawning tool — secondary modes are deliberately read-only.
_READ_ONLY_INVESTIGATION_TOOLS: list[str] = [
    "daytona_grep",
    "daytona_glob",
    "daytona_read_file",
    "ci_query_symbol",
    "ci_diagnostics",
    "ci_workspace_structure",
]

PLAN_FOR_HANDOFF_BRIEFING = """\
You have entered plan_for_handoff mode. This is a one-way commitment: the only
way out is to call submit_plan_handoff with a complete DAG plan.

Purpose
  Decompose the task into a DAG of child executors. Your output is the plan
  itself — the evaluator will validate the children's combined work against
  the acceptance_criteria you submit.

Allowed tools (read-only investigation)
  - daytona_read_file, daytona_grep, daytona_glob
  - ci_query_symbol, ci_diagnostics, ci_workspace_structure

Terminal tool
  - submit_plan_handoff — submit the DAG plan and exit this mode.

Required fields on submit_plan_handoff
  - tasks: flat DAG entries {id, deps}; transitive deps are implicit.
  - task_specs: map of id -> {title, spec} for every task above.
  - acceptance_criteria: the closure contract the evaluator will check.
  - handoff_note: articulate what the plan covers, what risks remain, and
    which acceptance_criteria items are most fragile. The evaluator reads
    this before validating child outputs.

You cannot edit, write, run shell commands, spawn subagents, or call any
other terminal in this mode. The dispatcher will reject any tool that is
not in the allowed list above. To leave this mode, call
submit_plan_handoff with a well-formed plan.
"""


PREPARE_CONTINUE_TO_WORK_BRIEFING = """\
You have entered prepare_continue_to_work mode. This is a one-way commitment:
the only way out is to call submit_continue_to_work with a gap summary.

Purpose
  You have judged the parent task's acceptance_criteria as not yet satisfied.
  Prepare the gap analysis that will drive the continuation executor — your
  summary is its input.

Allowed tools (read-only investigation)
  - daytona_read_file, daytona_grep, daytona_glob
  - ci_query_symbol, ci_diagnostics, ci_workspace_structure

Terminal tool
  - submit_continue_to_work — submit the gap summary and exit this mode.

Required field on submit_continue_to_work
  - summary: which acceptance_criteria items remain unmet, what evidence
    proves the gap, and what the continuation executor should focus on.

You cannot edit, write, run shell commands, spawn subagents, or call any
other terminal in this mode. The dispatcher will reject any tool that is
not in the allowed list above. To leave this mode, call
submit_continue_to_work with a gap summary.
"""


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
        # Direct mode is the open toolset: anything in the agent's runtime
        # registry is allowed except the entries/terminals that belong to
        # other modes (those presume a commitment that has not been made).
        ModeDefinition(
            name="direct",
            is_default=True,
            allowed_tools=None,
            disallowed_tools=[
                "submit_plan_handoff",
                "submit_continue_to_work",
                "enter_prepare_continue_to_work",
            ],
            terminals=["submit_task_completion"],
        ),
        ModeDefinition(
            name="plan_for_handoff",
            allowed_tools=list(_READ_ONLY_INVESTIGATION_TOOLS),
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
            allowed_tools=None,
            disallowed_tools=[
                "submit_plan_handoff",
                "enter_plan_for_handoff",
                "submit_continue_to_work",
            ],
            terminals=["submit_task_completion"],
        ),
        ModeDefinition(
            name="prepare_continue_to_work",
            allowed_tools=list(_READ_ONLY_INVESTIGATION_TOOLS),
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
    "PLAN_FOR_HANDOFF_BRIEFING",
    "PREPARE_CONTINUE_TO_WORK_BRIEFING",
    "register_builtin_agents",
]
