"""Plugin adapter — host-side ``call_plugin`` and in-sandbox runtime API.

This package is the *only* sandbox-side surface plugin authors are allowed to
import (per ``docs/architecture/plugins-refactor.md`` §2). It must remain
plugin-agnostic — no LSP-specific or language-specific code, no plugin-name
string switches.
"""

from __future__ import annotations

from typing import Any

__all__ = ["call_plugin"]


def __getattr__(name: str) -> Any:
    if name == "call_plugin":
        from sandbox.plugin.session import call_plugin

        return call_plugin
    raise AttributeError(name)
