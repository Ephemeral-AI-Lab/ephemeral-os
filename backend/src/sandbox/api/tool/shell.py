"""Public sandbox shell verb."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence

from sandbox.api.tool._runtime import (
    call_runtime_api,
    conflict_from_payload,
    int_from_payload,
    paths_from_payload,
    timings_from_payload,
)
from sandbox.api.utils.models import ConflictInfo, ShellRequest, ShellResult


async def shell(sandbox_id: str, request: ShellRequest) -> ShellResult:
    """Run a shell command through sandbox-local overlay and OCC."""
    total_start = time.perf_counter()
    if request.stdin is not None:
        return _error_result(
            reason="stdin_not_supported",
            message="snapshot overlay shell does not accept stdin",
            timings={"api.shell.total_s": time.perf_counter() - total_start},
        )

    raw = await call_runtime_api(
        sandbox_id,
        "api.shell",
        {
            "command": request.command,
            "cwd": _overlay_cwd(request.cwd),
            "timeout_seconds": request.timeout,
            "actor_id": request.caller.agent_id,
            "description": request.description or "shell",
        },
        timeout=(request.timeout or 60) + 30,
    )
    timings = timings_from_payload(raw.get("timings"))
    timings["api.shell.dispatch_total_s"] = time.perf_counter() - total_start
    return _result_from_payload(raw, timings=timings)


async def shell_batch(
    sandbox_id: str,
    requests: Sequence[ShellRequest],
    *,
    max_concurrency: int = 32,
    timeout: int | None = None,
) -> tuple[ShellResult, ...]:
    """Run multiple shell commands through one sandbox runtime dispatch."""
    if not requests:
        return ()

    results: list[ShellResult | None] = [None] * len(requests)
    dispatch_items: list[tuple[int, ShellRequest]] = []
    for index, request in enumerate(requests):
        if request.stdin is not None:
            results[index] = _error_result(
                reason="stdin_not_supported",
                message="snapshot overlay shell does not accept stdin",
            )
        else:
            dispatch_items.append((index, request))

    if dispatch_items:
        total_start = time.perf_counter()
        raw = await call_runtime_api(
            sandbox_id,
            "api.shell_batch",
            {
                "max_concurrency": max_concurrency,
                "items": [
                    {
                        "command": request.command,
                        "cwd": _overlay_cwd(request.cwd),
                        "timeout_seconds": request.timeout,
                        "actor_id": request.caller.agent_id,
                        "description": request.description or "shell",
                    }
                    for _, request in dispatch_items
                ],
            },
            timeout=timeout or _batch_timeout(dispatch_items, max_concurrency),
        )
        batch_timings = timings_from_payload(raw.get("timings"))
        batch_timings["api.shell_batch.dispatch_total_s"] = (
            time.perf_counter() - total_start
        )
        for (index, _), item in zip(
            dispatch_items,
            _payload_results(raw.get("results")),
            strict=True,
        ):
            item_timings = {
                **timings_from_payload(item.get("timings")),
                **batch_timings,
            }
            results[index] = _result_from_payload(item, timings=item_timings)

    return tuple(_require_result(result) for result in results)


def _result_from_payload(
    raw: Mapping[str, object],
    *,
    timings: dict[str, float],
) -> ShellResult:
    conflict = conflict_from_payload(raw.get("conflict"))
    return ShellResult(
        success=bool(raw.get("success", False)),
        exit_code=int_from_payload(raw.get("exit_code"), default=1),
        stdout=str(raw.get("stdout", "")),
        stderr=str(raw.get("stderr", "")),
        changed_paths=paths_from_payload(raw.get("changed_paths")),
        status=str(raw.get("status", "")),
        conflict=conflict,
        conflict_reason=(
            str(raw.get("conflict_reason"))
            if raw.get("conflict_reason") is not None
            else None
        ),
        warnings=paths_from_payload(raw.get("warnings")),
        timings=timings,
    )


def _error_result(
    *,
    reason: str,
    message: str,
    timings: dict[str, float] | None = None,
) -> ShellResult:
    conflict = ConflictInfo(reason=reason, message=message)
    return ShellResult(
        success=False,
        exit_code=1,
        stdout="",
        stderr="",
        changed_paths=(),
        status="error",
        conflict=conflict,
        conflict_reason=message,
        warnings=(),
        timings=timings or {},
    )


def _overlay_cwd(cwd: str | None) -> str:
    if cwd is None or not str(cwd).strip():
        return "."
    if str(cwd).startswith("/"):
        return "."
    return str(cwd)


def _batch_timeout(
    items: Sequence[tuple[int, ShellRequest]],
    max_concurrency: int,
) -> int:
    max_call_timeout = max((request.timeout or 60) for _, request in items)
    waves = (len(items) + max(1, max_concurrency) - 1) // max(1, max_concurrency)
    return int(max_call_timeout * waves + 60)


def _payload_results(raw: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise TypeError("shell_batch runtime response must contain a results list")
    results: list[Mapping[str, object]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise TypeError("shell_batch runtime response item must be an object")
        results.append(item)
    return tuple(results)


def _require_result(result: ShellResult | None) -> ShellResult:
    if result is None:
        raise RuntimeError("missing shell_batch result")
    return result


__all__ = ["shell", "shell_batch"]
