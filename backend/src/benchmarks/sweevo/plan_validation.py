"""Benchmark-specific plan validators for SWE-EVO runs.

These validators are injected via ``extra_validators`` into the generic
``validate_plan_phase_a`` at the call site (``submit_plan`` tool) — they
never run outside benchmark contexts.
"""

from __future__ import annotations

import os
import re
from typing import Any

Issue = dict[str, str]


def build_benchmark_payload_ref_validator(
    *,
    benchmark_test_ids: set[str],
    benchmark_test_files: set[str],
) -> "Callable":
    """Return a ``PlanItemValidator`` closure that checks payload refs.

    The returned callback has the signature
    ``(items: list[WorkItemSpec]) -> list[Issue]`` expected by
    ``validate_plan_phase_a(extra_validators=...)``.
    """
    from team.models import WorkItemSpec  # deferred to avoid circular imports

    basename_to_paths: dict[str, set[str]] = {}
    for path in benchmark_test_files:
        basename_to_paths.setdefault(os.path.basename(path), set()).add(path)

    def _looks_like_test_ref(value: str) -> bool:
        return (
            "::" in value
            or value.endswith(".py")
            or "/tests/" in value
            or value.startswith("tests/")
        )

    def _alias_issue(field: str, value: str, canonical: str) -> Issue:
        return {
            "field": field,
            "msg": (
                "benchmark reference must use the exact prompt path/id; "
                f"got {value!r}, expected {canonical!r}"
            ),
        }

    def _extract_py_paths(value: str) -> list[str]:
        return re.findall(
            r"(?<![A-Za-z0-9_./-])([A-Za-z0-9_./-]+\.py)(?![A-Za-z0-9_./-])",
            value,
        )

    def _validate(items: list[WorkItemSpec]) -> list[Issue]:
        issues: list[Issue] = []
        for idx, item in enumerate(items):
            payload: dict[str, Any] = item.payload if isinstance(item.payload, dict) else {}
            owned_failures = payload.get("owned_failures")
            if isinstance(owned_failures, list):
                for fi, raw in enumerate(owned_failures):
                    if not isinstance(raw, str):
                        continue
                    value = raw.strip()
                    if not value or not _looks_like_test_ref(value):
                        continue
                    if value in benchmark_test_ids or value in benchmark_test_files:
                        continue
                    file_candidate = (
                        value.split("::", 1)[0].strip() if "::" in value else value
                    )
                    if file_candidate in benchmark_test_files:
                        issues.append(
                            _alias_issue(
                                f"items[{idx}].payload.owned_failures[{fi}]",
                                value,
                                file_candidate,
                            )
                        )
                        continue
                    bmatches = basename_to_paths.get(
                        os.path.basename(file_candidate), set()
                    )
                    if len(bmatches) == 1:
                        issues.append(
                            _alias_issue(
                                f"items[{idx}].payload.owned_failures[{fi}]",
                                value,
                                next(iter(bmatches)),
                            )
                        )
                        continue
                    bmatches = basename_to_paths.get(
                        os.path.basename(value), set()
                    )
                    if len(bmatches) == 1:
                        issues.append(
                            _alias_issue(
                                f"items[{idx}].payload.owned_failures[{fi}]",
                                value,
                                next(iter(bmatches)),
                            )
                        )
                        continue
                    issues.append(
                        {
                            "field": f"items[{idx}].payload.owned_failures[{fi}]",
                            "msg": (
                                "benchmark reference must match an exact FAIL_TO_PASS/"
                                "PASS_TO_PASS node or exact benchmark test file path "
                                f"from the prompt, got {value!r}"
                            ),
                        }
                    )

            for key in ("reproduction", "verification", "verify", "retries"):
                raw_value = payload.get(key)
                if isinstance(raw_value, str):
                    values = [raw_value]
                elif isinstance(raw_value, list):
                    values = [v for v in raw_value if isinstance(v, str)]
                else:
                    values = []
                for vi, value in enumerate(values):
                    for path in _extract_py_paths(value):
                        if path in benchmark_test_files:
                            continue
                        bmatches = basename_to_paths.get(
                            os.path.basename(path), set()
                        )
                        if len(bmatches) == 1:
                            issues.append(
                                _alias_issue(
                                    f"items[{idx}].payload.{key}[{vi}]",
                                    path,
                                    next(iter(bmatches)),
                                )
                            )
        return issues

    return _validate
