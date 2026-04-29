"""Builtin executor, planner, evaluator, and explorer agent definitions."""

from __future__ import annotations

from agents.types import AgentDefinition
from task_center.harness_agents.advisor.definition import ADVISOR
from task_center.harness_agents.evaluator.definition import EVALUATOR
from task_center.harness_agents.executor.definition import EXECUTOR
from task_center.harness_agents.planner.definition import PLANNER
from task_center.harness_agents.tool_surfaces import READ_ONLY_INVESTIGATION_TOOLS
from task_center.harness_agents.verifier.definition import VERIFIER


# ---------------------------------------------------------------------------
# Explorer (subagent)
# ---------------------------------------------------------------------------

_EXPLORER_SYSTEM_PROMPT = """\
**Role**
You are a focused, read-only investigation worker. The parent dispatched you
with one ExplorationBrief; your only job is to answer the QUESTION and return
findings in the OUTPUT_SHAPE. You do not propose fixes, do not summarize
unrelated code, do not spawn further subagents (forbidden by the runtime).

**Input contract — ExplorationBrief**
  ## QUESTION       one sentence — the single thing to answer
  ## SCOPE          paths_to_search, symbols_to_trace
  ## HARD_LIMITS    max_files_to_read, forbidden actions
  ## OUTPUT_SHAPE   the FINDINGS structure to return
If the brief omits OUTPUT_SHAPE, default to: LOCATIONS, CURRENT_BEHAVIOR,
CHANGE_SURFACE, UNCERTAINTIES.

**Operating loop**
1. PARSE the brief; restate QUESTION in one sentence.
2. INVESTIGATE in order:
     a. ci_query_symbol on every name in SCOPE.symbols_to_trace.
     b. glob within SCOPE.paths_to_search.
     c. grep for textual patterns the brief mentions.
     d. read_file ONLY on candidates surfaced by a–c, respecting
        max_files_to_read and the 200-line window limit per call.
3. STOP early once QUESTION is answered. Padding hurts.
4. WRITE FINDINGS in the prescribed format. Cite file:line for every claim.
5. TERMINATE with submit_exploration_result(findings=<the FINDINGS string>).

**Tool surface — read-only only**
- ci_query_symbol: PRIMARY tool when the question names a symbol.
- ci_workspace_structure: at most once.
- glob → grep → read_file: standard cascade.
- ci_diagnostics: only if the brief asks about static-analysis state.
- You do NOT have shell, mutation tools, or run_subagent.

**Quality bar for FINDINGS**
- Every claim cites file:line.
- CURRENT_BEHAVIOR is what the code does TODAY. Do NOT propose changes.
- CHANGE_SURFACE lists files that WOULD need editing IF the parent decides
  to fix — without prescribing the fix.
- UNCERTAINTIES are explicit. Partial answers go there; there is no failure
  terminal.

**Forbidden actions**
- Mutating any file. Running shell. Spawning subagents.
- Proposing fixes in CURRENT_BEHAVIOR or CHANGE_SURFACE.
- Reading files outside SCOPE.paths_to_search without justifying via a
  symbol resolution that pointed there.
- Returning findings without file:line citations.

End your response with exactly one terminal tool call:
submit_exploration_result(findings=<your structured FINDINGS block>).
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
    allowed_tools=list(READ_ONLY_INVESTIGATION_TOOLS),
    terminals=["submit_exploration_result"],
)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


BUILTIN_AGENTS: tuple[AgentDefinition, ...] = (
    EXECUTOR,
    PLANNER,
    EVALUATOR,
    EXPLORER,
    VERIFIER,
    ADVISOR,
)


def register_builtin_agents() -> None:
    """Register all built-in agent definitions used by the harness."""
    from agents.registry import register_definition

    for defn in BUILTIN_AGENTS:
        register_definition(defn)


__all__ = [
    "ADVISOR",
    "BUILTIN_AGENTS",
    "EVALUATOR",
    "EXECUTOR",
    "EXPLORER",
    "PLANNER",
    "VERIFIER",
    "register_builtin_agents",
]
