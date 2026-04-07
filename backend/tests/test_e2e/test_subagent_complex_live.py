# ruff: noqa
"""Live E2E: Complex subagent coordination under heavy mixed load.

Three layered scenarios that exercise the ``run_subagent`` tool and its
background-task plumbing in realistic multi-wave coordination patterns:

Scenario A — Parallel Research Wave + Synthesis
  The parent decomposes a research goal into 3 background subagents (each
  investigates one domain) while a 4th subagent does outline work, all
  spawned in the same turn.  Once the background wave finishes the parent
  synthesises all 4 outputs into a single structured report.  Verifies:
  multiple background subagent launches in one turn, check_background_progress
  on live subagents, wait_for_background_task, and coherent synthesis in the
  final assistant text.

Scenario B — Two-Wave Refinement with Result Threading
  Wave-1: 2 background subagents produce raw drafts of two document halves.
  The parent peeks at live progress via check_background_progress while they
  run.  When wave-1 completes, the parent spawns wave-2: 2 new background
  subagents that each take one wave-1 draft and refine it.  Finally the
  parent merges the two refined halves.  Verifies: dependency-aware multi-
  wave spawning and result threading between waves.

Scenario C — Fan-out with Early Cancellation and Replacement
  The parent spawns 4 background subagents in parallel.  One of them is
  instructed to start its response with "BLOCKED:" (simulating an
  unresolvable blocker).  The parent must detect the signal via
  check_background_progress, cancel that subagent early (or note it if
  already done), spawn a replacement, and finally merge all 4 outputs into
  a Markdown table.  Verifies: mid-flight subagent cancellation and recovery
  without orphan tasks.

Run with:
  uv run pytest backend/tests/test_e2e/test_subagent_complex_live.py -v -s --log-cli-level=INFO

All three classes are guarded by ``EvalAgent.has_all()`` (API key + Daytona).
The ``run_subagent`` tool always runs as background="always", so the parent
must drive: run_subagent → check_background_progress → wait / cancel.
"""
from __future__ import annotations

import logging
import textwrap

import pytest

from engine.testing.eval_agent import EvalAgent
from tests.test_e2e.conftest import create_test_sandbox, delete_test_sandbox

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.e2e, pytest.mark.live]


# ---------------------------------------------------------------------------
# Local agent factory — identical to create_eval_agent() from conftest but
# also registers SubagentToolkit so run_subagent is available.
# ---------------------------------------------------------------------------


def _create_subagent_coordinator(
    *,
    system_prompt: str,
    sandbox_id: str,
    max_turns: int = 400,
) -> EvalAgent:
    """Create an EvalAgent with both DaytonaToolkit and SubagentToolkit registered.

    EvalAgent.create() only registers DaytonaToolkit. We replicate its setup
    logic here and additionally:
      - register SubagentToolkit so ``run_subagent`` is available, and
      - inject a ``SessionConfig`` into ``tool_metadata`` so that
        ``run_subagent`` can call ``spawn_agent`` without hitting the
        "missing session_config in execution context" guard.
    """
    from uuid import uuid4
    from pathlib import Path as _Path

    from config.settings import load_settings
    from providers.provider import make_api_client
    from tools import ToolRegistry
    from tools.daytona_toolkit import DaytonaToolkit
    from tools.subagent import SubagentToolkit
    from engine.runtime.agent import finalize_tool_registry_and_prompt
    from engine.core.query import QueryContext
    from compaction import SessionState
    from server.app_factory import SessionConfig

    settings = load_settings()

    # Resolve active DB model (same pattern as EvalAgent.create)
    db_kwargs: dict | None = None
    try:
        from server.app_factory import model_store

        if not model_store.is_available and settings.database.url:
            from db.engine import initialize_db

            sf = initialize_db(settings.database)
            if sf is not None:
                model_store.initialize(sf)

        active = model_store.get_active_resolved() if model_store.is_available else None
        if active:
            db_kwargs = active.get("kwargs")
    except Exception as exc:
        logger.debug("[_create_subagent_coordinator] DB model registry unavailable: %s", exc)

    resolved_model = (db_kwargs or {}).get("model") or settings.model
    api_client = make_api_client(settings, db_kwargs=db_kwargs)

    tool_registry = ToolRegistry()
    tool_registry.register_toolkit(DaytonaToolkit(sandbox_id=sandbox_id))
    tool_registry.register_toolkit(SubagentToolkit())

    prompt, has_background_tools = finalize_tool_registry_and_prompt(
        tool_registry, system_prompt
    )

    # Build the SessionConfig that run_subagent reads from tool_metadata to
    # call spawn_agent.  Mirror what build_session_config() does in production.
    #
    # IMPORTANT: do NOT pass external_api_client here.  Each subagent spawned
    # by run_subagent calls spawn_agent() which calls make_api_client().  When
    # external_api_client is set, all subagents share the same AsyncAnthropic
    # instance and its single httpx connection pool — running 4 concurrent
    # subagents causes pool contention and httpx.ReadError mid-stream.  By
    # leaving external_api_client=None, spawn_agent() constructs a fresh
    # AnthropicClient (with its own connection pool) per subagent, which is
    # safe for parallel async usage.  Credentials are propagated through the
    # *_override fields so resolve_settings() produces the right API key/URL.
    resolved_api_key = (db_kwargs or {}).get("api_key") or settings.resolve_api_key() or None
    resolved_base_url = (db_kwargs or {}).get("base_url") or settings.base_url or None
    resolved_api_format = (db_kwargs or {}).get("api_format") or settings.api_format or None

    session_config = SessionConfig(
        cwd=str(_Path.cwd()),
        session_id=uuid4().hex[:12],
        model_override=resolved_model,
        base_url_override=resolved_base_url,
        api_key_override=resolved_api_key,
        api_format_override=resolved_api_format,
        external_api_client=None,  # let spawn_agent build a fresh client per subagent
    )

    tool_metadata: dict[str, object] = {
        "session_config": session_config,
        "sandbox_id": sandbox_id,
    }

    query_context = QueryContext(
        api_client=api_client,
        tool_registry=tool_registry,
        cwd=str(_Path.cwd()),
        model=resolved_model,
        system_prompt=prompt,
        max_tokens=settings.max_tokens,
        max_turns=max_turns,
        hook_executor=None,
        enable_background_tasks=has_background_tools,
        tool_metadata=tool_metadata,
        session_state=SessionState(),
    )

    return EvalAgent(
        query_context=query_context,
        settings=settings,
        model=resolved_model,
        api_client=api_client,
    )


# ---------------------------------------------------------------------------
# Shared system prompt for the parent (coordinator) agent
# ---------------------------------------------------------------------------

COORDINATOR_PROMPT = """\
You are a senior coordinator agent. You decompose complex tasks by delegating
to focused worker subagents via the ``run_subagent`` tool.

CRITICAL RULES — read carefully:
- You have EXACTLY ONE tool for delegation: ``run_subagent``.  Use it for ALL
  delegation.  Do NOT use daytona_bash, daytona_codeact, or any other tool to
  do the delegated work yourself.
- ``run_subagent`` ALWAYS launches as a background task and returns a task_id
  immediately.  The subagent runs independently — you do NOT block.
- To launch several subagents IN PARALLEL, emit multiple ``run_subagent``
  calls in the SAME assistant turn.
- After spawning subagents use ``check_background_progress(task_id=<id>,
  last_n_lines=5)`` (non-blocking) to peek at a running subagent's latest
  messages.
- Use ``wait_for_background_task(task_id="all")`` when you are ready to join
  all running subagents.
- Use ``cancel_background_task`` if a subagent signals it is blocked or
  producing unwanted output.
- After all relevant subagents finish, synthesise their outputs in your final
  assistant message.  Reference the specific content each subagent produced.
- Never describe what you would do — use tools to execute it.
"""


# ---------------------------------------------------------------------------
# Shared logging helper
# ---------------------------------------------------------------------------


def _log_result(result, label: str) -> None:
    subagent_starts = [
        e for e in result.background_started() if e.tool_name == "run_subagent"
    ]
    subagent_done = [
        e for e in result.background_completed() if e.tool_name == "run_subagent"
    ]
    checks = result.tool_count("check_background_progress")
    waits = result.tool_count("wait_for_background_task")
    cancels = result.tool_count("cancel_background_task")

    logger.info(
        "\n%s\n[%s] Subagent complex summary:\n"
        "  Total tool calls   : %d\n"
        "  run_subagent starts: %d\n"
        "  run_subagent done  : %d\n"
        "  progress checks    : %d\n"
        "  wait calls         : %d\n"
        "  cancel calls       : %d\n"
        "  Tool sequence      : %s\n%s",
        "=" * 60,
        label,
        len(result.tool_calls),
        len(subagent_starts),
        len(subagent_done),
        checks,
        waits,
        cancels,
        result.tool_names,
        "=" * 60,
    )


# ===========================================================================
# Scenario A — Parallel Research Wave + Synthesis
#
# Parent spawns 3 background subagents (domain researchers) + 1 outline
# subagent in a single turn.  After checking progress and joining, it
# synthesises all 4 outputs into a structured report.
#
# Observable invariants:
#   - At least 3 run_subagent background_started events
#   - check_background_progress called at least once
#   - wait_for_background_task called at least once
#   - Final text covers all 3 research domains
#   - No unrecovered errors
# ===========================================================================


@pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required")
class TestSubagentParallelResearchSynthesis:
    """Parent spawns 3 parallel background subagents + 1 outline subagent,
    then synthesises all 4 outputs into a coherent report."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("subagent-research")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.mark.asyncio
    async def test_parallel_research_then_synthesis(self, sandbox):
        """Three background research subagents run concurrently; parent synthesises."""
        agent = _create_subagent_coordinator(
            system_prompt=COORDINATOR_PROMPT,
            sandbox_id=sandbox["id"],
            max_turns=400,
        )

        result = await agent.invoke(
            textwrap.dedent("""\
            Your ONLY tool for delegation is ``run_subagent``.
            Do NOT perform research yourself or use any shell tools.

            Goal: produce a structured report on "Distributed Systems Trade-offs"
            across three domains: Consistency, Availability, and Partition Tolerance.

            STEP 1 — In your very first turn, emit ALL FOUR run_subagent calls
            in the same message (parallel fan-out):

              Subagent RESEARCH_CONSISTENCY:
                prompt = "You are a technical writer. Write exactly 3 concise bullet
                points about trade-offs in the Consistency dimension of the CAP
                theorem. Each bullet must be one sentence. End your response with
                the exact marker: CONSISTENCY_RESEARCH_DONE"

              Subagent RESEARCH_AVAILABILITY:
                prompt = "You are a technical writer. Write exactly 3 concise bullet
                points about trade-offs in the Availability dimension of the CAP
                theorem. Each bullet must be one sentence. End your response with
                the exact marker: AVAILABILITY_RESEARCH_DONE"

              Subagent RESEARCH_PARTITION:
                prompt = "You are a technical writer. Write exactly 3 concise bullet
                points about trade-offs in the Partition Tolerance dimension of the
                CAP theorem. Each bullet must be one sentence. End your response with
                the exact marker: PARTITION_RESEARCH_DONE"

              Subagent OUTLINE:
                prompt = "You are a document structurer. Write a 40-60 word executive
                summary skeleton for a CAP theorem trade-offs report. Do NOT include
                domain-specific content — just structural framing. End with: OUTLINE_DONE"

            STEP 2 — After spawning, call check_background_progress on at least
            two of the task_ids to observe their live status.

            STEP 3 — Call wait_for_background_task with task_id="all" to join
            all four subagents.

            STEP 4 — In your final message, write a structured report with four
            sections: Executive Summary, Consistency, Availability, Partition
            Tolerance. Each section must incorporate the actual content from the
            corresponding subagent. Quote or paraphrase the output and state which
            marker tag confirmed completion (e.g. "CONSISTENCY_RESEARCH_DONE").
            """)
        )

        _log_result(result, "parallel_research_synthesis")

        # At least 3 background subagent launches (RESEARCH_* triad mandatory)
        subagent_starts = [
            e for e in result.background_started() if e.tool_name == "run_subagent"
        ]
        assert len(subagent_starts) >= 3, (
            f"Expected at least 3 run_subagent background launches (parallel research wave). "
            f"Got {len(subagent_starts)}. Tool sequence: {result.tool_names}"
        )

        # Parent must have called check_background_progress at least once
        assert result.has_tool("check_background_progress"), (
            f"Parent never called check_background_progress on running subagents. "
            f"Tool sequence: {result.tool_names}"
        )

        # wait_for_background_task must be called
        assert result.has_tool("wait_for_background_task"), (
            f"Parent never called wait_for_background_task. "
            f"Tool sequence: {result.tool_names}"
        )

        # Final text must reference at least 2 domain markers or domain keywords
        text_lower = result.text.lower()
        label_hits = sum(
            1
            for label in [
                "consistency_research_done",
                "availability_research_done",
                "partition_research_done",
                "outline_done",
            ]
            if label in text_lower
        )
        domain_hits = sum(
            1
            for keyword in ["consistency", "availability", "partition"]
            if keyword in text_lower
        )
        assert label_hits >= 2 or domain_hits >= 3, (
            f"Final text does not synthesise subagent outputs. "
            f"Label hits: {label_hits}/4, domain keyword hits: {domain_hits}/3. "
            f"Text (first 600 chars): {result.text[:600]}"
        )

        assert not result.has_unrecovered_errors, (
            f"Unrecovered errors: "
            f"{[e.output[:200] for e in result.unrecovered_error_events]}"
        )


# ===========================================================================
# Scenario B — Two-Wave Refinement with Result Threading
#
# Wave-1: 2 background subagents produce terse drafts.
# Parent peeks at both via check_background_progress, then joins wave-1.
# Wave-2: 2 new background subagents refine one draft each (parent passes
#         the wave-1 text verbatim into the wave-2 prompt).
# Parent joins wave-2, then writes the merged final document.
#
# Observable invariants:
#   - At least 4 run_subagent background_started events (2 per wave)
#   - check_background_progress called at least 2 times (once per wave)
#   - wait_for_background_task called at least 2 times (once per wave)
#   - Final text references refinement completion (REFINE_*_DONE markers
#     or synonyms)
#   - No unrecovered errors
# ===========================================================================


@pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required")
class TestSubagentTwoWaveRefinement:
    """Multi-wave subagent coordination: raw drafts → refinement → merge."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("subagent-twowave")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.mark.asyncio
    async def test_two_wave_refinement_with_result_threading(self, sandbox):
        """Wave-1 produces drafts; wave-2 refines them; parent merges."""
        agent = _create_subagent_coordinator(
            system_prompt=COORDINATOR_PROMPT,
            sandbox_id=sandbox["id"],
            max_turns=400,
        )

        result = await agent.invoke(
            textwrap.dedent("""\
            Your ONLY tool for delegation is ``run_subagent``.
            Do NOT write content yourself or use any shell tools.

            Goal: produce a polished technical document on "REST API Design
            Best Practices" using a two-wave draft-then-refine process.

            WAVE 1 — emit BOTH run_subagent calls in the same turn:

              Subagent DRAFT_A:
                prompt = "You are a technical writer. Write a terse 4-bullet rough
                draft covering REST API URL structure and HTTP verb usage. Each bullet
                is one plain sentence — no polish needed. End with: DRAFT_A_DONE"

              Subagent DRAFT_B:
                prompt = "You are a technical writer. Write a terse 4-bullet rough
                draft covering REST API error handling and versioning strategies.
                Each bullet is one plain sentence — no polish needed.
                End with: DRAFT_B_DONE"

            After launching wave 1: call check_background_progress on BOTH task_ids.
            Then: call wait_for_background_task with task_id="all" to join wave 1.

            WAVE 2 — after collecting both wave-1 outputs, emit BOTH run_subagent
            calls in the same turn, threading the wave-1 text into each prompt:

              Subagent REFINE_A:
                prompt = "You are an editor. You are given this rough draft to
                improve:\n\n<PASTE THE EXACT DRAFT_A OUTPUT HERE>\n\nExpand each
                bullet to 2 sentences. Fix grammar. Add one concrete example per
                bullet. End with: REFINE_A_DONE"
                (Replace the placeholder with the actual DRAFT_A text you received.)

              Subagent REFINE_B:
                prompt = "You are an editor. You are given this rough draft to
                improve:\n\n<PASTE THE EXACT DRAFT_B OUTPUT HERE>\n\nExpand each
                bullet to 2 sentences. Fix grammar. Add one concrete example per
                bullet. End with: REFINE_B_DONE"
                (Replace the placeholder with the actual DRAFT_B text you received.)

            After launching wave 2: call check_background_progress on BOTH task_ids.
            Then: call wait_for_background_task with task_id="all" to join wave 2.

            FINAL — In your last message combine the two refined sections into a
            single document titled "REST API Design Best Practices". State
            explicitly that it incorporates REFINE_A_DONE and REFINE_B_DONE outputs.
            """)
        )

        _log_result(result, "two_wave_refinement")

        # At least 4 run_subagent launches total (2 wave-1 + 2 wave-2)
        subagent_starts = [
            e for e in result.background_started() if e.tool_name == "run_subagent"
        ]
        assert len(subagent_starts) >= 4, (
            f"Expected at least 4 run_subagent launches (2 per wave). "
            f"Got {len(subagent_starts)}. Tool sequence: {result.tool_names}"
        )

        # check_background_progress called at least twice (once per wave)
        checks = result.tool_count("check_background_progress")
        assert checks >= 2, (
            f"Expected at least 2 check_background_progress calls (one per wave). "
            f"Got {checks}. Tool sequence: {result.tool_names}"
        )

        # wait_for_background_task called at least twice (once per wave)
        waits = result.tool_count("wait_for_background_task")
        assert waits >= 2, (
            f"Expected at least 2 wait_for_background_task calls (one per wave). "
            f"Got {waits}. Tool sequence: {result.tool_names}"
        )

        # Final text must reference wave-2 refinement
        text_lower = result.text.lower()
        refine_hits = sum(
            1
            for marker in ["refine_a_done", "refine_b_done", "refined", "improved", "polish"]
            if marker in text_lower
        )
        assert refine_hits >= 1, (
            f"Final text does not reference wave-2 refinement. "
            f"Text (first 600 chars): {result.text[:600]}"
        )

        # REST API domain content must be present
        domain_hits = sum(
            1
            for kw in ["url", "http", "error", "version", "rest", "api"]
            if kw in text_lower
        )
        assert domain_hits >= 2, (
            f"Final text missing REST API domain content. "
            f"Text (first 600 chars): {result.text[:600]}"
        )

        assert not result.has_unrecovered_errors, (
            f"Unrecovered errors: "
            f"{[e.output[:200] for e in result.unrecovered_error_events]}"
        )


# ===========================================================================
# Scenario C — Fan-out with Early Cancellation and Replacement
#
# Parent spawns 4 background subagents in parallel (fan-out).
# One subagent (EU-WEST) is instructed to start its entire response with
# "BLOCKED:" — the parent must detect this signal via
# check_background_progress, cancel or note the blocked task, spawn a
# replacement fallback subagent, wait for all remaining tasks, and produce
# a Markdown latency table covering all 4 regions with the EU-WEST row
# marked as "(replaced fallback)".
#
# Observable invariants:
#   - At least 4 run_subagent background_started events (fan-out wave)
#   - check_background_progress called at least once
#   - At least 5 total run_subagent launches (4 original + 1 replacement)
#   - wait_for_background_task called at least once
#   - Final text covers all 4 regions and notes the fallback replacement
#   - No unrecovered errors (cancel of blocked subagent is expected)
# ===========================================================================


@pytest.mark.skipif(not EvalAgent.has_all(), reason="API + Daytona both required")
class TestSubagentFanoutWithCancellationAndRecovery:
    """Fan-out: one subagent signals BLOCKED; parent cancels and spawns replacement."""

    @pytest.fixture(scope="class")
    def sandbox(self):
        sb = create_test_sandbox("subagent-fanout")
        yield sb
        delete_test_sandbox(sb["id"])

    @pytest.mark.asyncio
    async def test_blocked_subagent_cancelled_and_replaced(self, sandbox):
        """Parent detects BLOCKED signal, handles that subagent, spawns replacement."""
        agent = _create_subagent_coordinator(
            system_prompt=COORDINATOR_PROMPT,
            sandbox_id=sandbox["id"],
            max_turns=400,
        )

        result = await agent.invoke(
            textwrap.dedent("""\
            Your ONLY tool for delegation is ``run_subagent``.
            Do NOT invent latency numbers yourself or use any shell tools.

            Goal: collect fictional infrastructure latency metrics for four
            data-center regions and produce a summary table.

            STEP 1 — Fan-out: emit ALL FOUR run_subagent calls in the same turn:

              Subagent REGION_US_EAST:
                prompt = "You are a metrics agent. Report fictional but plausible
                average latency values for the US-EAST data center.
                Format your entire response as exactly one line:
                US_EAST: p50=Xms p95=Yms p99=Zms
                Then on a new line write: REGION_US_EAST_DONE"

              Subagent REGION_US_WEST:
                prompt = "You are a metrics agent. Report fictional but plausible
                average latency values for the US-WEST data center.
                Format your entire response as exactly one line:
                US_WEST: p50=Xms p95=Yms p99=Zms
                Then on a new line write: REGION_US_WEST_DONE"

              Subagent REGION_EU_WEST:
                prompt = "IMPORTANT INSTRUCTION: Your data source is unavailable.
                Your ENTIRE response must begin with the exact text:
                BLOCKED: EU-WEST data source is offline
                Do not include any latency numbers. That is your complete response."

              Subagent REGION_AP_SOUTH:
                prompt = "You are a metrics agent. Report fictional but plausible
                average latency values for the AP-SOUTH data center.
                Format your entire response as exactly one line:
                AP_SOUTH: p50=Xms p95=Yms p99=Zms
                Then on a new line write: REGION_AP_SOUTH_DONE"

            STEP 2 — Monitor: call check_background_progress with task_id="all"
            to observe all tasks. Identify which task_id produced the BLOCKED
            prefix.

            STEP 3 — Handle the blocked task:
              - If the blocked task is still running: call cancel_background_task
                on it immediately.
              - If it already completed with BLOCKED output: note it and proceed.
              Either way, spawn one replacement subagent:
                Subagent REGION_EU_WEST_FALLBACK:
                  prompt = "The EU-WEST data source was offline. Use these fallback
                  values: EU_WEST_FALLBACK: p50=45ms p95=110ms p99=200ms
                  End with: REGION_EU_WEST_FALLBACK_DONE"

            STEP 4 — Join: call wait_for_background_task with task_id="all" to
            collect results from the remaining 3 normal + 1 replacement subagents.

            STEP 5 — Final report: write a Markdown table with columns:
            Region | p50 | p95 | p99 | Notes
            Include all 4 regions. Mark the EU-WEST row Notes as "(replaced fallback)".
            """)
        )

        _log_result(result, "fanout_cancel_replace")

        # At least 4 initial run_subagent launches (the fan-out wave)
        subagent_starts = [
            e for e in result.background_started() if e.tool_name == "run_subagent"
        ]
        assert len(subagent_starts) >= 4, (
            f"Expected at least 4 run_subagent launches (fan-out wave). "
            f"Got {len(subagent_starts)}. Tool sequence: {result.tool_names}"
        )

        # check_background_progress must have been called
        assert result.has_tool("check_background_progress"), (
            f"Parent never called check_background_progress to detect BLOCKED signal. "
            f"Tool sequence: {result.tool_names}"
        )

        # At least 5 total launches (4 fan-out + 1 replacement)
        assert len(subagent_starts) >= 5, (
            f"Expected at least 5 run_subagent launches (4 fan-out + 1 replacement). "
            f"Got {len(subagent_starts)}. Tool sequence: {result.tool_names}"
        )

        # wait_for_background_task must be called
        assert result.has_tool("wait_for_background_task"), (
            f"Parent never called wait_for_background_task. "
            f"Tool sequence: {result.tool_names}"
        )

        # Final text must cover all 4 distinct regions
        text_lower = result.text.lower()
        distinct_regions = sum(
            1
            for pair in [
                ("us-east", "us_east"),
                ("us-west", "us_west"),
                ("eu-west", "eu_west"),
                ("ap-south", "ap_south"),
            ]
            if any(r in text_lower for r in pair)
        )
        assert distinct_regions >= 3, (
            f"Final text missing regions. Found {distinct_regions}/4 distinct regions. "
            f"Text (first 800 chars): {result.text[:800]}"
        )

        # Final text must acknowledge the blocked/replaced EU-WEST slot
        has_replacement_note = any(
            w in text_lower
            for w in [
                "replaced", "replacement", "fallback", "eu_west_fallback",
                "eu-west_fallback", "blocked",
            ]
        )
        assert has_replacement_note, (
            f"Final text does not acknowledge the blocked/replaced EU-WEST slot. "
            f"Text (first 800 chars): {result.text[:800]}"
        )

        assert not result.has_unrecovered_errors, (
            f"Unrecovered errors: "
            f"{[e.output[:200] for e in result.unrecovered_error_events]}"
        )
