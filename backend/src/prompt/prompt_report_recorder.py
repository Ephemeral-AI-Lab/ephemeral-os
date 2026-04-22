"""Small prompt-report recorder with per-context sequencing."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Mapping

from prompt.message_recorder import append_prompt_report_event

logger = logging.getLogger(__name__)


class PromptReportRecorder:
    """Append prompt-report events with a monotonically increasing sequence."""

    def __init__(
        self,
        path: str | Path | None,
        *,
        base_event: Mapping[str, Any] | None = None,
    ) -> None:
        self._path = path
        self._base_event = dict(base_event or {})
        self._seq = 0

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def record(self, event: Mapping[str, Any]) -> None:
        if not self._path:
            return
        try:
            append_prompt_report_event(
                self._path,
                {
                    **self._base_event,
                    **dict(event),
                },
            )
        except Exception:
            logger.debug("prompt report append failed", exc_info=True)


__all__ = ["PromptReportRecorder"]
