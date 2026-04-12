"""Worker pull loop. Pops ready WorkItems and drives them through execute_with_posthook."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable
from typing import TYPE_CHECKING, Any, Callable

from agents.registry import has_role as _has_role
from hooks.agent_posthook import NoPosthookOutput, execute_with_posthook
from team.models import AgentResult, Plan, ReplanPlan
from team.runtime.context_builder import TeamAgentContext
from tools.posthook.types import PosthookSubmission, ReplanRequest, RetryRequest, SubmittedSummary


def _is_reviewer(agent_name: str) -> bool:
    return _has_role(agent_name, "reviewer")

if TYPE_CHECKING:
    from agents.types import AgentDefinition
    from team.models import WorkItem
    from team.runtime.team_run import TeamRun


# ---------------------------------------------------------------------------
# Extensible submission → dispatch-result conversion
# ---------------------------------------------------------------------------

SubmissionConverter = Callable[[Any], "AgentResult | RetryRequest | ReplanRequest"]

_SUBMISSION_CONVERTERS: dict[str, SubmissionConverter] = {}


def register_submission_converter(kind: str, converter: SubmissionConverter) -> None:
    """Register a converter for a new ``submission_kind``.

    This allows new posthook submission types to participate in the
    executor dispatch without modifying ``_result_from_submission``.
    """
    _SUBMISSION_CONVERTERS[kind] = converter


def _default_converters() -> None:
    """Register the built-in converters on first import."""

    def _convert_summary(sub: Any) -> AgentResult:
        return AgentResult(artifact=sub.artifact, summary=sub.summary)

    def _convert_retry(sub: Any) -> RetryRequest:
        return sub

    def _convert_replan(sub: Any) -> ReplanRequest:
        return sub

    register_submission_converter("summary", _convert_summary)
    register_submission_converter("retry", _convert_retry)
    register_submission_converter("replan", _convert_replan)


_default_converters()

logger = logging.getLogger(__name__)

QueryRunner = Callable[["AgentDefinition", Any], Awaitable[Any]]
QueryContextBuilder = Callable[["AgentDefinition", "TeamRun", "WorkItem"], TeamAgentContext]
PosthookContextBuilder = Callable[["AgentDefinition", Any], TeamAgentContext]


class Executor:
    """Runtime invariant: every team agent MUST submit through a posthook.

    Either ``Plan`` (planner) or ``SubmittedSummary`` (worker / scout /
    validator). Anything else fails the WorkItem with a grep-able reason.
    Wire ``submit_summary_agent`` as the default posthook for any agent
    that does not have a domain-specific submission.
    """

    def __init__(
        self,
        team_run: "TeamRun",
        runner: QueryRunner,
        build_query_context: QueryContextBuilder,
        build_posthook_context: PosthookContextBuilder,
        agent_lookup: Callable[[str], "AgentDefinition | None"],
        after_dispatch: Callable[["WorkItem", AgentResult, list["WorkItem"]], Any] | None = None,
    ) -> None:
        self.team_run = team_run
        self.runner = runner
        self.build_query_context = build_query_context
        self.build_posthook_context = build_posthook_context
        self.agent_lookup = agent_lookup
        self.after_dispatch = after_dispatch

    async def _checkpoint_after_transition(
        self,
        wi: "WorkItem",
        *,
        outcome: str,
    ) -> None:
        """Persist a post-dispatch checkpoint after the dispatcher state mutates."""
        try:
            label = f"durable:{outcome}:{wi.agent_name}:{wi.local_id or wi.id}"
            await self.team_run.checkpoint(label=label)
        except Exception:
            logger.debug("Failed to checkpoint after %s transition for %s", outcome, wi.id, exc_info=True)

    async def run_forever(self) -> None:
        """Pop READY items until cancel_event is set.

        Workers MUST NOT exit just because the graph is momentarily terminal —
        a peer worker may still complete a planner that submits a fresh Plan,
        re-populating the queue. Only ``TeamRun`` decides when workers stop,
        via ``cancel_event``.
        """
        dispatcher = self.team_run.dispatcher
        while not self.team_run.cancel_event.is_set():
            try:
                wi_id = await asyncio.wait_for(dispatcher.pop_ready(), timeout=0.1)
            except asyncio.TimeoutError:
                continue

            try:
                await self._run_one(wi_id)
            except Exception as exc:  # worker never dies
                logger.exception("Worker error on %s: %s", wi_id, exc)
                await dispatcher.fail(wi_id, f"worker_exception: {exc}")

    @staticmethod
    def _result_from_submission(submitted: Any) -> AgentResult | RetryRequest | ReplanRequest | None:
        if submitted is None:
            return None

        # Protocol-based dispatch: any type implementing PosthookSubmission
        # can register a converter via register_submission_converter().
        if isinstance(submitted, PosthookSubmission):
            kind = submitted.submission_kind
            converter = _SUBMISSION_CONVERTERS.get(kind)
            if converter is not None:
                return converter(submitted)

        # Team-specific types that don't implement the protocol (Plan,
        # ReplanPlan) are handled here as a fallback.
        if isinstance(submitted, Plan):
            return AgentResult(artifact=None, summary="", submitted_plan=submitted)
        if isinstance(submitted, ReplanPlan):
            return AgentResult(artifact=None, summary="", submitted_replan=submitted)

        raise TypeError(type(submitted).__name__)

    async def _run_one(self, wi_id: str) -> None:
        dispatcher = self.team_run.dispatcher
        agent_run_id = str(uuid.uuid4())
        wi = await dispatcher.mark_running(wi_id, agent_run_id)

        defn = self.agent_lookup(wi.agent_name)
        if defn is None:
            await dispatcher.fail(wi_id, f"unknown_agent: {wi.agent_name}")
            return

        query_ctx = self.build_query_context(defn, self.team_run, wi)
        try:
            # work_result is consumed for posthook side-effects only; the
            # final dispatch result is built from ``submitted`` below.
            execution = execute_with_posthook(
                work_defn=defn,
                work_ctx=query_ctx,
                runner=self.runner,
                agent_lookup=self.agent_lookup,
                posthook_ctx_builder=self.build_posthook_context,
            )
            _, submitted = await execution
        except NoPosthookOutput as exc:
            await dispatcher.fail(wi_id, f"NoPosthookOutput: {exc}")
            return

        try:
            dispatch_payload = self._result_from_submission(submitted)
        except TypeError as exc:
            await dispatcher.fail(wi_id, f"unexpected_submission_type: {exc}")
            return

        if dispatch_payload is None:
            await dispatcher.fail(
                wi_id,
                "no_posthook_submission: team agents must submit via a posthook "
                "(use submit_summary_agent if no domain-specific posthook applies)",
            )
            return
        artifact = dispatch_payload.artifact if isinstance(dispatch_payload, AgentResult) else None
        self.team_run.note_context_access(
            work_item=wi,
            metadata=query_ctx.tool_metadata,
            artifact=artifact if isinstance(artifact, dict) else None,
        )
        if isinstance(dispatch_payload, RetryRequest):
            await dispatcher.retry_work_item(wi_id, dispatch_payload)
            await self._checkpoint_after_transition(wi, outcome="retry")
            return
        if isinstance(dispatch_payload, ReplanRequest):
            await dispatcher.request_replan(wi_id, dispatch_payload)
            await self._checkpoint_after_transition(wi, outcome="replan_request")
            return

        new_items = await dispatcher.complete(wi_id, dispatch_payload)
        if isinstance(dispatch_payload, AgentResult):
            self.team_run.note_explicit_memory_artifacts(
                work_item=wi,
                artifact=dispatch_payload.artifact,
            )
            if _is_reviewer(wi.agent_name):
                self.team_run.note_validator_outcome(
                    work_item=wi,
                    summary=dispatch_payload.summary,
                    artifact=dispatch_payload.artifact,
                )
        if self.after_dispatch is not None:
            callback_result = self.after_dispatch(wi, dispatch_payload, new_items)
            if isinstance(callback_result, Awaitable):
                await callback_result
        await self._checkpoint_after_transition(wi, outcome="complete")
