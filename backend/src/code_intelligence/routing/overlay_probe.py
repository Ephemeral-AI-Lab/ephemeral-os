"""Detect whether per-run overlayfs auditing is available on a sandbox.

Runs one short probe command inside ``unshare -Urm`` with a tmpfs-backed
upperdir and ``userxattr`` overlay. Returns :data:`True` only when every
step of the :class:`OverlayExec` pipeline succeeds -- this is the exact
recipe exercised by live ``OverlayExec`` commands, so a positive probe
is a strong guarantee the auditor will work for real workloads.

Results are cached per-sandbox so the probe pays at most once per
sandbox lifetime.
"""

from __future__ import annotations

import logging
import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_PROBE_TIMEOUT = 30


_PROBE_SCRIPT = r"""
set +e
WORK=$(mktemp -d /tmp/overlayprobe-XXXXXX) || { echo PROBE_FAIL:mktemp; exit 1; }
cd "$WORK"
mkdir -p ns || { echo PROBE_FAIL:mkdir; exit 1; }
unshare -Urm --propagation private bash -c '
    set +e
    mount -t tmpfs -o size=16m tmpfs ns 2> err || { cat err; echo PROBE_FAIL:tmpfs; exit 1; }
    mkdir -p ns/lo ns/up ns/wk ns/mg
    echo base > ns/lo/a
    mount -t overlay overlay -o lowerdir=ns/lo,upperdir=ns/up,workdir=ns/wk,userxattr ns/mg 2>> err \
        || { cat err; echo PROBE_FAIL:overlay; exit 1; }
    echo modified > ns/mg/a
    [ "$(cat ns/up/a 2>/dev/null)" = "modified" ] || { echo PROBE_FAIL:upperdir; exit 1; }
    echo PROBE_OK
'
RC=$?
rm -rf "$WORK" 2>/dev/null
exit $RC
"""


@dataclass(frozen=True)
class OverlayProbeResult:
    supported: bool
    reason: str
    """Short human-readable reason: ``"ok"`` on success, or the failing
    step identifier (``tmpfs``, ``overlay``, ``upperdir``, etc.)."""


async def probe_overlay_capability(
    sandbox: Any,
    exec_process: Callable[..., Awaitable[Any]],
    *,
    timeout: int = _PROBE_TIMEOUT,
) -> OverlayProbeResult:
    """Run the overlay capability probe once against ``sandbox``.

    Parameters
    ----------
    sandbox:
        The Daytona sandbox object (or compatible).
    exec_process:
        Same async exec callable the auditor uses; passed in so the
        probe exercises the real transport.
    timeout:
        Hard timeout in seconds. Probe should complete in well under
        a second on a healthy sandbox.
    """
    command = f"bash -c {shlex.quote(_PROBE_SCRIPT)}"
    try:
        response = await exec_process(sandbox, command, timeout=timeout)
    except Exception as exc:
        logger.warning("overlay probe transport failed: %s", exc)
        return OverlayProbeResult(supported=False, reason=f"transport:{exc}")

    raw = str(getattr(response, "result", "") or "")
    if "PROBE_OK" in raw:
        return OverlayProbeResult(supported=True, reason="ok")

    for token in ("PROBE_FAIL:tmpfs", "PROBE_FAIL:overlay", "PROBE_FAIL:upperdir",
                  "PROBE_FAIL:mktemp", "PROBE_FAIL:mkdir"):
        if token in raw:
            return OverlayProbeResult(
                supported=False,
                reason=token.split(":", 1)[1],
            )
    return OverlayProbeResult(supported=False, reason="unknown")


class OverlayCapabilityCache:
    """Cache one probe result per sandbox id.

    Not thread-safe; service-level locking is expected to serialize
    concurrent probes against a single sandbox. Different sandboxes can
    be probed in parallel.
    """

    def __init__(self) -> None:
        self._results: dict[str, OverlayProbeResult] = {}

    def get(self, sandbox_id: str) -> OverlayProbeResult | None:
        return self._results.get(sandbox_id)

    async def probe(
        self,
        sandbox_id: str,
        sandbox: Any,
        exec_process: Callable[..., Awaitable[Any]],
        *,
        force: bool = False,
    ) -> OverlayProbeResult:
        if not force:
            cached = self._results.get(sandbox_id)
            if cached is not None:
                return cached
        result = await probe_overlay_capability(sandbox, exec_process)
        self._results[sandbox_id] = result
        return result

    def invalidate(self, sandbox_id: str) -> None:
        self._results.pop(sandbox_id, None)


__all__ = [
    "OverlayCapabilityCache",
    "OverlayProbeResult",
    "probe_overlay_capability",
]
