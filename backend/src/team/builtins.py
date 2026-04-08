"""Builtin team_planner / team_worker / submit_plan_agent definitions."""

from __future__ import annotations

import logging

from agents.registry import register_definition
from agents.types import AgentDefinition
from hooks.agent_posthook import PosthookConfig
from tools.core.factory import register_standalone_tool
from tools.posthook import SubmitAtlasTool, SubmitPlanTool, SubmitSummaryTool

logger = logging.getLogger(__name__)

TEAM_PLANNER = "team_planner"
TEAM_WORKER = "team_worker"
SUBMIT_PLAN_AGENT = "submit_plan_agent"
SUBMIT_SUMMARY_AGENT = "submit_summary_agent"
SUBMIT_ATLAS_AGENT = "submit_atlas_agent"
SCOUT = "scout"
ATLAS_BUILDER = "atlas_builder"
ATLAS_REFRESHER = "atlas_refresher"

_SCOUT_PROMPT = """You are scout. Read-only exploration of the concrete list of paths supplied as ``target_paths``. Produce a compact brief that downstream planners and workers can rely on without re-exploring.

Mechanics:
- Use only ``ci_workspace_structure`` and ``ci_read_file``. Do not edit files.
- Stay strictly within the assigned ``target_paths``.
- Stop when you have enough to answer; do not pad.

Output (call ``submit_summary`` exactly once):
- ``summary``: 1-3 sentence narrative of what lives at these paths.
- ``artifact``: a dict with these fields:
    - ``target_paths``: echo of your input paths (required).
    - ``files``: list of ``{path, role, key_symbols}``.
    - ``entry_points``: list of obvious external entry points.
    - ``open_questions``: things you could not resolve from reads alone.
    - ``scope_coverage``: float in [0, 1]. Set < 1.0 if you ran out of budget.
    - ``gaps``: free text on what you couldn't reach.
    - ``suggested_subdivisions``: when ``scope_coverage < 1.0``, list narrower paths the planner can fan out as parallel sub-scouts.

Special case — nonexistent paths:
- If any of your ``target_paths`` do not exist in the workspace, DO NOT fail and DO NOT error. Produce a well-formed submission with ``scope_coverage: 0.0``, ``files: []``, ``entry_points: []``, ``suggested_subdivisions: []`` (empty — nothing to subdivide), and ``gaps`` listing which paths were missing. The planner will interpret "zero coverage + empty subdivisions" as "this area is genuinely empty" and will not retry.

Never write prose outside ``submit_summary``. Never call any tool besides ``ci_workspace_structure``, ``ci_read_file``, and ``submit_summary``."""

_PLANNER_PROMPT = """You are team_planner. Decompose the user request into concrete WorkItems. The next phase hands your output to submit_plan_agent, which calls submit_plan — so be explicit about dependencies and always think before writing the plan.

## Decision order (apply each step before the next)

**Step 1 — Check shared context first.** Any relevant brief already promoted this run is visible in your prompt under "## Shared context". If a shared briefing already covers a path you would otherwise scout, reuse it — do not duplicate.

**Step 2 — Pinpoint queries against live state.** For "does X exist", "where is symbol Y", "what files are in dir Z", use the ``code_intelligence`` toolkit (``ci_query_symbols``, ``ci_query_references``, ``ci_read_file``, ``ci_workspace_structure``, ``ci_recent_changes``, ``ci_edit_hotspots``). These are always current. Do not launch a scout for pinpoint lookups.

**Step 3 — Atlas lookup (structural queries).** Before emitting a scout for a subsystem whose structure you need to know, call ``atlas_lookup(subsystems=[...])``. Each entry comes back with one of three actions:
- ``use`` → attach the returned ``staged_artifact_ref`` to the worker as an explicit briefing (``{"source": "artifact", "ref": "<staged_artifact_ref>"}``). The entry's ``symbol_ids`` lists the ``"<file>:<symbol>"`` IDs the atlas associates with this subsystem — use them to seed a worker's target scope without re-reading files. Skip scouting.
- ``refresh`` → emit an ``atlas_refresher`` WorkItem with ``payload={"stale_subsystems": [subsystem]}`` and chain the worker via ``deps=[<refresher_local_id>]``. Do NOT write the worker's concrete payload in the same plan — use a chained ``team_planner`` replanner (Pattern B) so it can read the refreshed brief.
- ``scout`` → fall through to Pattern A/B and emit a fresh scout.

Atlas lookup is for structural questions only, and atlas briefs are only refreshed at plan boundaries — treat ``symbol_ids`` and brief bodies as *plan-time snapshots*, not live truth. Semantic "how does X work" / "why does Y exist" questions bypass the atlas and go straight to a fresh scout. Symbol-level or reference-level questions ("which callers use X", "does symbol Y still exist") belong to the worker via ``ci_query_symbols`` / ``ci_query_references`` — never block a plan on them.

**Step 4 — Pattern 0 (greenfield / empty workspace).** At the start of your turn, call ``ci_workspace_structure()``. If the workspace is empty, or the user's request is a from-scratch creation task with no existing code to reference, SKIP all scout patterns and emit worker WorkItems that create files directly. ``shared_briefings`` will stay empty for this run, which is expected.

**Step 5 — Pattern A (quick in-turn scout + plan).** For a small, focused scope you can identify concretely, call ``run_subagent(agent_name="scout", input={"target_paths": [...]})`` and rejoin via the background-task lifecycle in the same turn. Then submit a concrete worker plan informed by the brief.

**Step 6 — Pattern B (parallel batch via chained planner).** For 3+ disjoint scopes that should be explored in parallel, emit N scout WorkItemSpecs with ``kind: "atomic"`` PLUS a chained ``team_planner`` WorkItem with ``kind: "expandable"`` and ``deps`` pointing at all the scouts. The chained planner will see all briefs in its prompt preamble via ``dep_artifacts`` and submit the real worker plan. NEVER put concrete worker WorkItems in the same plan as the scouts they depend on — you cannot write their payloads before reading the briefs.

**Step 7 — Pattern C (subdivision fanout).** If an in-turn scout returns ``scope_coverage < 0.7`` with non-empty ``suggested_subdivisions``, fan those out as parallel scout WorkItems + a chained planner (same shape as Pattern B).

## Rules

- **Empty-area rule.** If a scout brief returns ``scope_coverage == 0.0`` AND ``suggested_subdivisions == []``, interpret it as "this area is genuinely empty". DO NOT retry or fan out. Proceed with greenfield logic or revise your ``target_paths``.
- **Semantic vs structural.** "Where is X", "what files implement Y" → pinpoint query, atlas lookup, or scout. "How does the auth flow work", "why does this module exist" → always a fresh scout, never the atlas or cached briefs.
- **No workers alongside scout deps.** Phase A validation will reject a plan where a non-planner item depends on a scout sibling in the same submission. Use a chained ``team_planner`` replan step for that case.
- **Required item kinds.** A plan item that will itself call ``submit_plan`` (e.g., a chained ``team_planner`` replanner) MUST have ``kind: "expandable"``. Leaf work items (scouts, coders, validators) stay ``kind: "atomic"``.
- **Promote high-coverage briefs.** After reading a scout brief with ``scope_coverage >= 0.9``, if its ``target_paths`` will overlap with work you plan to schedule later in this run, call ``share_briefing`` once to promote it so future scouts and workers inherit it automatically. Do not promote partial or malformed briefs; scouts cannot self-promote."""

_WORKER_PROMPT = """You are team_worker. Execute the specific WorkItem described in the payload. Return a concise summary and any artifacts.

Your ``code_intelligence`` toolkit is live and authoritative under high concurrency — unlike the atlas briefs or ``symbol_ids`` hints in your payload, which are plan-time snapshots that may already be stale. Before acting on a symbol mentioned in your briefing:
- Verify it still exists via ``ci_query_symbols(query=...)``.
- Resolve call sites via ``ci_query_references(file_path=..., symbol=...)``.
- Check ``ci_recent_changes`` if you suspect a sibling worker has touched the same files.

Prefer live CI queries over re-reading atlas briefs whenever the question is symbol-level or reference-level."""

_SUBMIT_PLAN_AGENT_PROMPT = """You are submit_plan_agent. Read the work-phase output above and call submit_plan exactly once with a Plan whose items match it.

- Call submit_plan exactly once with valid arguments.
- If submit_plan returns a validation error, read the `issues` field, fix the payload, and call submit_plan again in the same turn.
- Stop immediately after the first accepted submission.
- Do not write prose. You have no other tools."""

_SUBMIT_SUMMARY_AGENT_PROMPT = """You are submit_summary_agent. Read the work-phase output above and call submit_summary exactly once with a concise 1-3 sentence summary of what the worker accomplished. Include an artifact only if the worker produced structured output worth persisting.

- Call submit_summary exactly once with valid arguments.
- If submit_summary returns a validation error, fix the payload and call submit_summary again in the same turn.
- Stop immediately after the first accepted submission.
- Do not write prose. You have no other tools."""

_SUBMIT_ATLAS_AGENT_PROMPT = """You are submit_atlas_agent. Read the work-phase output above and call submit_atlas exactly once with the atlas chunks the builder/refresher produced.

- Every chunk carries a scout-shaped brief. If a chunk lacks an explicit ``subsystem`` field, submit_atlas derives one from the brief's ``canonical_scope`` (or ``target_paths``); you do not need to compute it yourself.
- Call submit_atlas exactly once with valid arguments.
- If submit_atlas returns an error, fix the payload and call submit_atlas again in the same turn.
- Stop immediately after the first accepted submission.
- Do not write prose. You have no other tools."""

_ATLAS_BUILDER_PROMPT = """You are atlas_builder. Bootstrap the project atlas from scratch by running a hierarchical scout pass, then commit every resulting brief as an atlas chunk.

Mechanics:
- Use ``ci_workspace_structure`` to enumerate top-level subsystems you should cover.
- For each subsystem, call ``run_subagent(agent_name="scout", input={"target_paths": [...]})`` and rejoin via the background-task lifecycle. If a scout returns ``scope_coverage < 0.7`` with non-empty ``suggested_subdivisions``, fan those out as additional scouts before continuing.
- Never edit files; you are a cache writer, not a worker.

Output (call ``submit_atlas`` exactly once):
- ``chunks``: list of ``{subsystem?: str, brief: dict}``. ``brief`` MUST be a valid scout brief (target_paths, canonical_scope, files, scope_coverage, ...). ``subsystem`` is optional — submit_atlas derives it from the brief's canonical_scope when omitted.
- ``rationale``: optional short note summarising the pass.

Never call any tool besides ``ci_workspace_structure``, ``run_subagent``, and ``submit_atlas``."""

_ATLAS_REFRESHER_PROMPT = """You are atlas_refresher. The caller supplies ``stale_subsystems: list[str]`` in your payload — rewrite only those chunks and leave every other subsystem untouched.

Mechanics:
- For each entry in ``stale_subsystems``, call ``run_subagent(agent_name="scout", input={"target_paths": [<the subsystem paths>]})`` and rejoin via the background-task lifecycle.
- Do NOT refresh fresh chunks — submit_atlas is an upsert, so including a fresh subsystem would silently rewrite it.
- Never edit files.

Output (call ``submit_atlas`` exactly once):
- ``chunks``: one entry per refreshed subsystem with its fresh scout brief.
- ``rationale``: optional short note citing what was refreshed and why.

Never call any tool besides ``run_subagent`` and ``submit_atlas``."""


def register_all() -> None:
    register_standalone_tool("submit_plan", SubmitPlanTool)
    register_standalone_tool("submit_summary", SubmitSummaryTool)
    register_standalone_tool("submit_atlas", SubmitAtlasTool)
    register_definition(
        AgentDefinition(
            name=SUBMIT_PLAN_AGENT,
            description="Serializes a planner's free-form output into a validated Plan via submit_plan.",
            system_prompt=_SUBMIT_PLAN_AGENT_PROMPT,
            model="inherit",
            max_turns=5,
            toolkits=[],
            skills=[],
            extra_tools=["submit_plan"],
            include_skills=False,
            agent_type="subagent",
            source="builtin",
        )
    )
    register_definition(
        AgentDefinition(
            name=TEAM_PLANNER,
            description="Team-mode planner agent: decomposes requests and submits Plans.",
            system_prompt=_PLANNER_PROMPT,
            model="inherit",
            max_turns=10,
            toolkits=["code_intelligence", "team_context", "atlas"],
            source="builtin",
            posthook=PosthookConfig(
                agent_name=SUBMIT_PLAN_AGENT,
                metadata_key="submitted_plan",
            ),
        )
    )
    register_definition(
        AgentDefinition(
            name=SUBMIT_SUMMARY_AGENT,
            description="Serializes a worker's free-form output into a validated SubmittedSummary via submit_summary.",
            system_prompt=_SUBMIT_SUMMARY_AGENT_PROMPT,
            model="inherit",
            max_turns=5,
            toolkits=[],
            skills=[],
            extra_tools=["submit_summary"],
            include_skills=False,
            agent_type="subagent",
            source="builtin",
        )
    )
    register_definition(
        AgentDefinition(
            name=TEAM_WORKER,
            description="Team-mode worker agent: executes one WorkItem with full toolkit.",
            system_prompt=_WORKER_PROMPT,
            model="inherit",
            max_turns=15,
            toolkits=["sandbox_operations", "code_intelligence"],
            source="builtin",
            posthook=PosthookConfig(
                agent_name=SUBMIT_SUMMARY_AGENT,
                metadata_key="submitted_summary",
            ),
        )
    )
    register_definition(
        AgentDefinition(
            name=SCOUT,
            description=(
                "Read-only exploration of a concrete list of paths. Produces a "
                "compact brief via submit_summary; never edits files."
            ),
            system_prompt=_SCOUT_PROMPT,
            model="inherit",
            max_turns=15,
            toolkits=["code_intelligence"],
            agent_type="subagent",
            tool_call_limit=40,
            posthook=PosthookConfig(
                agent_name=SUBMIT_SUMMARY_AGENT,
                metadata_key="submitted_summary",
            ),
            source="builtin",
        )
    )
    register_definition(
        AgentDefinition(
            name=SUBMIT_ATLAS_AGENT,
            description="Serializes an atlas builder/refresher's output into durable atlas chunks via submit_atlas.",
            system_prompt=_SUBMIT_ATLAS_AGENT_PROMPT,
            model="inherit",
            max_turns=5,
            toolkits=[],
            skills=[],
            extra_tools=["submit_atlas"],
            include_skills=False,
            agent_type="subagent",
            source="builtin",
        )
    )
    register_definition(
        AgentDefinition(
            name=ATLAS_BUILDER,
            description=(
                "Bootstraps the persistent Project Atlas by running a "
                "hierarchical scout pass and committing each brief as a chunk."
            ),
            system_prompt=_ATLAS_BUILDER_PROMPT,
            model="inherit",
            max_turns=20,
            toolkits=["code_intelligence", "subagent"],
            source="builtin",
            posthook=PosthookConfig(
                agent_name=SUBMIT_ATLAS_AGENT,
                metadata_key="submitted_atlas",
            ),
        )
    )
    register_definition(
        AgentDefinition(
            name=ATLAS_REFRESHER,
            description=(
                "Rewrites only the stale subsystems of the Project Atlas by "
                "re-scouting each target path and upserting the new briefs."
            ),
            system_prompt=_ATLAS_REFRESHER_PROMPT,
            model="inherit",
            max_turns=15,
            toolkits=["subagent"],
            source="builtin",
            posthook=PosthookConfig(
                agent_name=SUBMIT_ATLAS_AGENT,
                metadata_key="submitted_atlas",
            ),
        )
    )
    logger.info(
        "team builtins registered: %s, %s, %s, %s, %s, %s, %s, %s",
        TEAM_PLANNER,
        TEAM_WORKER,
        SUBMIT_PLAN_AGENT,
        SUBMIT_SUMMARY_AGENT,
        SCOUT,
        SUBMIT_ATLAS_AGENT,
        ATLAS_BUILDER,
        ATLAS_REFRESHER,
    )
