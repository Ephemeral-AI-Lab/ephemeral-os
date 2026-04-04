"""Fetch and summarize remote web pages."""

from __future__ import annotations

import re

import httpx
from pydantic import BaseModel, Field

from ephemeralos.tools.base import BaseTool, ToolExecutionContext, ToolResult


class WebFetchToolInput(BaseModel):
    """Arguments for fetching one web page."""

    url: str = Field(description="HTTP or HTTPS URL to fetch")
    max_chars: int = Field(default=12000, ge=500, le=50000)


class WebFetchTool(BaseTool):
    """Fetch one web page and return a compact text summary."""

    name = "web_fetch"
    description = "Fetch one web page and return compact readable text."
    input_model = WebFetchToolInput

    async def execute(self, arguments: WebFetchToolInput, context: ToolExecutionContext) -> ToolResult:
        del context
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=20.0) as client:
                response = await client.get(arguments.url, headers={"User-Agent": "EphemeralOS/0.1"})
                response.raise_for_status()
        except httpx.HTTPError as exc:
            return ToolResult(output=f"web_fetch failed: {exc}", is_error=True)

        content_type = response.headers.get("content-type", "")
        body = response.text
        if "html" in content_type:
            body = _html_to_text(body)
        body = body.strip()
        if len(body) > arguments.max_chars:
            body = body[: arguments.max_chars].rstrip() + "\n...[truncated]"
        return ToolResult(
            output=(
                f"URL: {response.url}\n"
                f"Status: {response.status_code}\n"
                f"Content-Type: {content_type or '(unknown)'}\n\n"
                f"{body}"
            )
        )

    def is_read_only(self, arguments: BaseModel) -> bool:
        del arguments
        return True


def _html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"[ \t\r\f\v]+", " ", text).replace(" \n", "\n").strip()
