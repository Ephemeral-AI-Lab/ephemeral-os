"""Single source of truth for terminal-tool semantics.

See ``registry.py`` for the ``TerminalToolDescriptor`` model and the
``TERMINAL_DESCRIPTORS`` registry consumed by both main-agent ``user_msg_2``
rendering (``selection_guidance``) and advisor ``user_msg_2`` rendering
(``advisor_review_focus``).
"""

from __future__ import annotations

from tools._terminals.registry import (
    TERMINAL_DESCRIPTORS,
    TerminalToolDescriptor,
    render_terminal_catalog,
)

__all__ = [
    "TERMINAL_DESCRIPTORS",
    "TerminalToolDescriptor",
    "render_terminal_catalog",
]
