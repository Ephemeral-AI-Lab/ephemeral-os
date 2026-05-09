"""Plugin discovery, manifest parsing, and tool registration.

Manifest parsing has zero coupling to the tool system; that's why it lives
under ``plugins/core/`` rather than ``tools/`` — keeps the catalog
self-contained per ``docs/architecture/plugins-refactor.md`` §3.
"""

from __future__ import annotations

__all__: list[str] = []
