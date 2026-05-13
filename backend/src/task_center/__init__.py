"""TaskCenter internals. External callers should import ``task_center.api``.

Import policy:
- **External callers** (outside ``task_center.*``): import from
  ``task_center.api``.
- **Internal callers** (inside ``task_center.*``): import from the canonical
  module (e.g. ``task_center.mission.mission`` for ``MissionCloseReport``).
- ``task_center.domain`` re-exports a curated public subset for legacy and
  cross-package consumers; treat it as equivalent to ``api`` for read-only
  access to domain types.
"""
