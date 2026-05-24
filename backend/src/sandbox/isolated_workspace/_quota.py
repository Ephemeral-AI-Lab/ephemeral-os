"""Quota, host-capacity, and manager-state loading helpers."""

from __future__ import annotations

import json
from typing import Any

from sandbox.isolated_workspace._types import SCHEMA_VERSION, IsolatedWorkspaceError, logger


class _IsolatedQuotaMixin:
        def _check_host_capacity(self) -> None:
            budget = self._compute_host_budget()
            required = (len(self._handles) + 1) * self._config.upperdir_bytes
            if required > budget:
                raise IsolatedWorkspaceError(
                    "host_capacity_exceeded",
                    "host RAM gate refuses new isolated workspace",
                    required_bytes=required, budget_bytes=budget,
                )

        def _compute_host_budget(self) -> int:
            try:
                memavail_kb = self._meminfo_reader()
            except Exception:
                return 2**62
            return int(memavail_kb * 1024 * self._config.memavail_fraction)

        def _read_manager_json(self) -> dict[str, Any]:
            path = self.manager_json_path
            if not path.exists():
                return {"schema_version": SCHEMA_VERSION, "handles": []}
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                logger.warning("manager_json_unreadable path=%s", path)
                return {"schema_version": SCHEMA_VERSION, "handles": []}
            if data.get("schema_version") != SCHEMA_VERSION:
                logger.warning("manager_json_schema_mismatch expected=%s found=%s",
                               SCHEMA_VERSION, data.get("schema_version"))
                return {"schema_version": SCHEMA_VERSION, "handles": []}
            return data


__all__ = ["_IsolatedQuotaMixin"]
