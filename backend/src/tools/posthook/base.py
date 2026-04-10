"""Base class for posthook submit tools.

A "submit" tool is the single exit point of a posthook serializer agent:
it takes the work-phase output, validates it, and stashes the validated
payload in ``context.metadata`` under the slot named by
``posthook_metadata_key``. ``execute_with_posthook`` reads that slot to
know the posthook succeeded.

This module factors the boilerplate shared by every such tool — metadata
key lookup, double-submit guarding, error framing — so concrete tools
(``SubmitPlanTool``, ``SubmitSummaryTool``, ...) only have to implement
the domain-specific ``_build_payload`` step.
"""

from __future__ import annotations

from abc import abstractmethod
import json
from typing import Any

from pydantic import BaseModel

from tools.core.base import BaseTool, ToolExecutionContext, ToolResult


def _decode_json_array_string(value: Any) -> Any:
    """Best-effort decode for serializer agents that pass JSON arrays as text."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value

    if text.startswith("["):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

    decoder = json.JSONDecoder()
    best_payload: list[Any] | None = None
    best_start: int | None = None
    best_end = -1
    for start, char in enumerate(text):
        if char != "[":
            continue
        try:
            payload, end = decoder.raw_decode(text, idx=start)
        except ValueError:
            continue
        if not isinstance(payload, list):
            continue
        if end > best_end or (end == best_end and (best_start is None or start < best_start)):
            best_payload = payload
            best_start = start
            best_end = end
    return best_payload if best_payload is not None else value


class SubmitPosthookTool(BaseTool):
    """Abstract submit tool for posthook serializer agents.

    Subclasses set ``name``, ``description``, ``input_model``, and
    ``default_metadata_key``, then implement ``_build_payload`` to turn
    validated input into the domain object that gets stored in
    ``context.metadata[posthook_metadata_key]``. On success they may
    override ``_accepted_message`` to customize the tool result text.
    """

    default_metadata_key: str = "submitted_output"

    @abstractmethod
    def _build_payload(
        self, arguments: BaseModel, context: ToolExecutionContext
    ) -> tuple[Any, str | None]:
        """Validate *arguments* and return ``(payload, error)``.

        If ``error`` is non-None the tool fails the call with that message
        (without stashing anything). Otherwise ``payload`` is stored under
        the metadata key and the call succeeds.
        """

    def _accepted_message(self, payload: Any) -> str:  # noqa: ARG002
        """Override to customize the success message."""
        return "Submission accepted."

    async def execute(
        self, arguments: BaseModel, context: ToolExecutionContext
    ) -> ToolResult:
        metadata = context.metadata
        key = metadata.get("posthook_metadata_key", self.default_metadata_key)

        if metadata.get(key) is not None:
            return ToolResult(
                output=f"{self.name} already called; second call ignored.",
                is_error=True,
            )

        payload, error = self._build_payload(arguments, context)
        if error is not None:
            return ToolResult(output=error, is_error=True)

        metadata[key] = payload
        return ToolResult(output=self._accepted_message(payload))
