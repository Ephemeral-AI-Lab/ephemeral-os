"""TaskCenter internals. External callers should import ``task_center.api``.

Import policy:
- **External callers** (outside ``task_center.*``): import from
  ``task_center.api``.
- **Internal callers** (inside ``task_center.*``): import from the canonical
  module (e.g. ``task_center.mission.mission`` for ``MissionCloseReport``).
- ``task_center.domain`` is a narrow read-only DTO facade for persistence,
  audits, and legacy cross-package domain consumers.
"""
