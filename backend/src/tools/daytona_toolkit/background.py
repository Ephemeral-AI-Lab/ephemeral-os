"""Daytona-specific background launch preparation.

The engine's background launch path is tool-agnostic and does NOT sniff
tool names. When a tool needs physical cancel semantics (i.e. killing an
OS process in a sandbox), that logic lives here rather than in
``engine/core/query.py``. The engine calls
:func:`prepare_background_launch` unconditionally for every background
dispatch; the function is a no-op for tools it does not handle.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def prepare_background_launch(
    tool_name: str,
    tool_input: dict[str, Any],
    task_id: str,
    sandbox: Any | None,
) -> tuple[dict[str, Any], None]:
    """Return ``(prepared_input, kill_callback)`` for a background launch.

    All tools get their input returned unchanged with ``None`` for the
    callback. The engine calls this unconditionally so it never has to
    know which tools need special handling.
    """
    return tool_input, None
