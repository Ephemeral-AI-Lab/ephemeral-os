"""Provider-neutral operations performed against a sandbox.

These modules are built on raw provider primitives (``provider.exec`` and the
``ProviderAdapter`` surface from :mod:`sandbox.providers.protocol`). They never
import from any specific provider package — that's what makes the orchestrator
provider-agnostic.

Layer rule: ``ops`` may import from :mod:`sandbox.control.daemon`; the reverse
is forbidden.
"""

from __future__ import annotations

__all__: list[str] = []
