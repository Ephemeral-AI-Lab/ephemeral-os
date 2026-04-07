"""Anthropic-native streaming client for EphemeralOS.

Uses the official ``anthropic`` Python SDK directly. The key advantage over the
OpenAI-compatible client is that tool-use blocks are yielded as
``ApiToolUseDeltaEvent`` on ``content_block_stop`` (mid-stream), so tools can
begin executing while the model is still generating subsequent content blocks.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

import anthropic

from providers.types import (
    ApiMessageCompleteEvent,
    ApiMessageRequest,
    ApiStreamEvent,
    ApiTextDeltaEvent,
    ApiThinkingDeltaEvent,
    ApiToolUseDeltaEvent,
    UsageSnapshot,
)
from providers.errors import (
    AuthenticationFailure,
    EphemeralOSApiError,
    RateLimitFailure,
    RequestFailure,
)
from message import assistant_message_from_api

log = logging.getLogger(__name__)

MAX_RETRIES = 3
BASE_DELAY = 1.0
MAX_DELAY = 30.0


class AnthropicClient:
    """Anthropic-native streaming client.

    Implements the ``SupportsStreamingMessages`` protocol using the official
    ``anthropic`` async SDK.  Tool-use content blocks are emitted mid-stream
    on ``content_block_stop`` so the engine can start tool execution early.
    """

    def __init__(self, api_key: str, *, base_url: str | None = None) -> None:
        kwargs: dict[str, Any] = {}
        if base_url:
            kwargs["base_url"] = base_url

        # Non-Anthropic endpoints (e.g. MiniMax) expect Authorization: Bearer
        # instead of Anthropic's x-api-key header.
        if base_url and "anthropic.com" not in base_url:
            kwargs["auth_token"] = api_key
        else:
            kwargs["api_key"] = api_key

        self._client = anthropic.AsyncAnthropic(**kwargs)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def aclose(self) -> None:
        """Gracefully close the underlying HTTP transport."""
        await self._client.close()

    # ------------------------------------------------------------------
    # Public interface (SupportsStreamingMessages)
    # ------------------------------------------------------------------

    async def stream_message(self, request: ApiMessageRequest) -> AsyncIterator[ApiStreamEvent]:
        """Yield streamed events for *request* with retry logic."""
        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES + 1):
            try:
                async for event in self._stream_once(request):
                    yield event
                return
            except EphemeralOSApiError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt >= MAX_RETRIES or not self._is_retryable(exc):
                    raise self._translate_error(exc) from exc

                delay = min(BASE_DELAY * (2**attempt), MAX_DELAY)
                log.warning(
                    "Anthropic API request failed (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1,
                    MAX_RETRIES + 1,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)

        if last_error is not None:
            raise self._translate_error(last_error) from last_error

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _stream_once(self, request: ApiMessageRequest) -> AsyncIterator[ApiStreamEvent]:
        """Single streaming attempt against the Anthropic API."""
        messages = [msg.to_api_param() for msg in request.messages]

        # Strip output_schema — the Anthropic API does not accept it in tool defs.
        tools = (
            [{k: v for k, v in t.items() if k != "output_schema"} for t in request.tools]
            if request.tools
            else []
        )

        params: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "system": request.system_prompt or "",
            "max_tokens": request.max_tokens,
        }
        if tools:
            params["tools"] = tools

        # Track content blocks by index for reassembly.
        collected_content_blocks: dict[int, dict[str, Any]] = {}

        async with self._client.messages.stream(**params) as stream:
            async for event in stream:
                event_type = event.type

                if event_type == "content_block_start":
                    block = event.content_block
                    collected_content_blocks[event.index] = {
                        "type": block.type,
                        "id": getattr(block, "id", ""),
                        "name": getattr(block, "name", ""),
                        "text": "",
                        "input_json": "",
                    }

                elif event_type == "content_block_delta":
                    delta = event.delta
                    idx = event.index
                    block_state = collected_content_blocks[idx]

                    if delta.type == "text_delta":
                        block_state["text"] += delta.text
                        yield ApiTextDeltaEvent(text=delta.text)

                    elif delta.type == "thinking_delta":
                        block_state["text"] += delta.thinking
                        yield ApiThinkingDeltaEvent(text=delta.thinking)

                    elif delta.type == "input_json_delta":
                        block_state["input_json"] += delta.partial_json

                elif event_type == "content_block_stop":
                    idx = event.index
                    block_state = collected_content_blocks[idx]

                    if block_state["type"] == "tool_use":
                        # KEY: yield tool event MID-STREAM with complete args
                        try:
                            args = (
                                json.loads(block_state["input_json"])
                                if block_state["input_json"]
                                else {}
                            )
                        except (json.JSONDecodeError, TypeError):
                            args = {}
                        yield ApiToolUseDeltaEvent(
                            id=block_state["id"],
                            name=block_state["name"],
                            input=args,
                        )

            # After the stream ends, build the final message from the SDK.
            final_msg = await stream.get_final_message()

        message = assistant_message_from_api(final_msg)

        yield ApiMessageCompleteEvent(
            message=message,
            usage=UsageSnapshot(
                input_tokens=final_msg.usage.input_tokens,
                output_tokens=final_msg.usage.output_tokens,
            ),
            stop_reason=final_msg.stop_reason,
        )

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        """Return True if the exception is transient and worth retrying."""
        if isinstance(exc, anthropic.APIStatusError):
            return exc.status_code in {429, 500, 502, 503, 529}
        if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
            return True
        return False

    @staticmethod
    def _translate_error(exc: Exception) -> EphemeralOSApiError:
        """Map upstream exceptions to EphemeralOS error hierarchy."""
        status = getattr(exc, "status_code", None)
        msg = str(exc)
        if status in {401, 403}:
            return AuthenticationFailure(msg)
        if status == 429:
            return RateLimitFailure(msg)
        return RequestFailure(msg)
