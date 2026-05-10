"""SWE-EVO live e2e compat shim.

The generic framework lives at :mod:`live_e2e`. This package keeps the legacy
``benchmarks.sweevo.live_test.*`` import paths green by re-exporting from
``live_e2e`` and layering SWE-EVO-specific fixtures (``sweevo_instance``,
``sweevo_sandbox``) and the SWE-EVO entry-prompt builder on top.

See ``docs/wiki/live-e2e-testing-framework-design.md`` for the migration plan.
"""

from __future__ import annotations

__all__: list[str] = []
