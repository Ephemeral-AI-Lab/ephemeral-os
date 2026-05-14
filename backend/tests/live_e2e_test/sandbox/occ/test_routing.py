"""Phase 1b native probes for OCC route decisions."""

from __future__ import annotations

import pytest

from .._harness.native_cases import run_native_case
from .._harness.sandbox_fixture import SandboxHandle


pytestmark = pytest.mark.asyncio


_ROUTING_BODY = r"""
from sandbox.occ.changeset.prepared import CommitOptions, RouteDecision
from sandbox.occ.changeset.types import OpaqueDirChange, SymlinkChange, WriteChange
from sandbox.occ.changeset.builders import build_api_write_change, build_overlay_write_change

def write_change(*, path, final_content, source="api_write", base_hash=None):
    if source == "overlay_capture":
        return build_overlay_write_change(
            path=path,
            final_content=final_content,
        ).with_base_hash(base_hash)
    return build_api_write_change(
        path=path,
        final_content=final_content,
        base_hash=base_hash,
    )

from sandbox.occ.router import Router

class _Gitignore:
    def __init__(self):
        self.ignored = {"dist/app.js", "cache", "ignored-link"}
        self.calls = []
    def is_ignored(self, path):
        self.calls.append(path)
        return path in self.ignored

label = "occ.routing"
before = sample_resource()
started = time.perf_counter()
gitignore = _Gitignore()
router = Router(gitignore)
prepared = router.prepare_sync(
    [
        write_change(path="src/app.py", final_content=b"x"),
        write_change(path="dist/app.js", final_content=b"x"),
        write_change(path=".git/config", final_content=b"x"),
        write_change(path="../escape", final_content=b"x"),
        SymlinkChange(path="ignored-link", target="/tmp/data"),
        OpaqueDirChange(path="cache", kept_children=frozenset({"keep"})),
    ],
    snapshot=None,
    options=CommitOptions(),
)
routes = [(group.path, group.route.value) for group in prepared.path_groups]
assert routes == [
    ("src/app.py", RouteDecision.GATED.value),
    ("dist/app.js", RouteDecision.DIRECT.value),
    (".git/config", RouteDecision.DROP.value),
    ("../escape", RouteDecision.REJECT.value),
    ("ignored-link", RouteDecision.DIRECT.value),
    ("cache", RouteDecision.DIRECT.value),
]
assert gitignore.calls == ["src/app.py", "dist/app.js", "ignored-link", "cache"]

_emit(label, started, before, {
    "routes": routes,
    "gitignore_calls": gitignore.calls,
    "drop_message": prepared.path_groups[2].message,
    "reject_message": prepared.path_groups[3].message,
})
"""


async def test_routing_applies_direct_gated_drop_reject_and_override_priority(
    native_sandbox: SandboxHandle,
) -> None:
    payload = await run_native_case(
        native_sandbox,
        _ROUTING_BODY,
        label="occ.routing",
    )
    assert payload["routes"][0] == ["src/app.py", "gated"]
    assert payload["routes"][1] == ["dist/app.js", "direct"]
    assert payload["routes"][2] == [".git/config", "drop"]
    assert payload["routes"][3] == ["../escape", "reject"]
