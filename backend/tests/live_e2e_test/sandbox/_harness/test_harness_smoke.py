"""Phase-0 harness gate test (deleted at end of phase).

Confirms the native-probe rendering path is wired correctly:

- ``native_sandbox`` fixture brings up the runtime bundle and asserts
  ``/eos/daemon/.bundle-hash`` exists.
- A probe rendered by ``native_probe.render`` runs ``cd
  /eos/daemon && python3 -c "<source>"`` and is able to
  import ``sandbox.layer_stack``, ``sandbox.occ``, and ``sandbox.overlay``
  from the bundle.
- The ``RESOURCE_PRELUDE`` ``sample_resource()`` helper emits a §3.5
  resource block that this host-side test can parse.
"""

from __future__ import annotations

import json

import pytest

from .native_probe import (
    BUNDLE_HASH_MARKER,
    BUNDLE_REMOTE_DIR,
    render,
    shell_command,
)
from .sandbox_fixture import SandboxHandle


_SMOKE_BODY = r"""
cfg = json.loads(__CFG_JSON__)

errors = []
imported = {}
for module_name in cfg["modules"]:
    try:
        mod = __import__(module_name, fromlist=["*"])
    except Exception as exc:  # noqa: BLE001
        errors.append({
            "module": module_name,
            "error": "%s: %s" % (type(exc).__name__, exc),
            "trace": traceback.format_exc(),
        })
        continue
    # Namespace packages (PEP 420) have __file__ = None; fall back to __path__.
    location = getattr(mod, "__file__", None)
    if not location:
        try:
            paths = list(mod.__path__)
        except AttributeError:
            paths = []
        location = paths[0] if paths else None
    imported[module_name] = location

before = sample_resource(cfg["inode_path"])
# Trivial workload: list the bundle dir so wall_ms moves and fd briefly opens.
listing = sorted(os.listdir(cfg["bundle_dir"]))[:8]
after = sample_resource(cfg["inode_path"])

bundle_hash = None
try:
    with open(cfg["bundle_hash_marker"]) as fh:
        bundle_hash = fh.read().strip()
except OSError as exc:
    errors.append({
        "module": "<bundle-hash>",
        "error": "%s: %s" % (type(exc).__name__, exc),
    })

print(json.dumps({
    "bundle_dir": cfg["bundle_dir"],
    "bundle_hash": bundle_hash,
    "imported": imported,
    "errors": errors,
    "listing_head": listing,
    "resource_before": before,
    "resource_after": after,
}, separators=(",", ":")))
"""


@pytest.mark.asyncio
async def test_native_probe_imports_runtime_bundle(
    native_sandbox: SandboxHandle,
) -> None:
    cfg = {
        "modules": ["sandbox.layer_stack", "sandbox.occ", "sandbox.overlay"],
        "bundle_dir": BUNDLE_REMOTE_DIR,
        "bundle_hash_marker": BUNDLE_HASH_MARKER,
        "inode_path": BUNDLE_REMOTE_DIR,
    }
    cmd = shell_command(render(_SMOKE_BODY, cfg=cfg))
    result = await native_sandbox.raw_exec(
        native_sandbox.sandbox_id, cmd, timeout=60
    )
    assert result.exit_code == 0, (
        f"native smoke probe failed (rc={result.exit_code}): "
        f"stderr={result.stderr!r} stdout={result.stdout!r}"
    )

    payload = json.loads(result.stdout.strip().splitlines()[-1])
    print(f"\n[harness.smoke] {json.dumps(payload, separators=(',', ':'))}")

    assert payload["errors"] == [], payload["errors"]
    assert payload["bundle_hash"], payload
    assert set(payload["imported"]) == {
        "sandbox.layer_stack",
        "sandbox.occ",
        "sandbox.overlay",
    }, payload["imported"]
    for module_name, file_path in payload["imported"].items():
        assert file_path and file_path.startswith(BUNDLE_REMOTE_DIR), (
            f"{module_name} resolved outside the bundle: {file_path!r}"
        )

    for label in ("resource_before", "resource_after"):
        block = payload[label]
        assert isinstance(block, dict), block
        for key in (
            "fd_open",
            "rss_kb",
            "rss_peak_kb",
            "threads",
            "mounts",
            "overlay_mounts",
            "wall_ms",
            "cpu_user_ms",
            "cpu_sys_ms",
        ):
            assert key in block, (label, key, block)
        assert block["mounts"] >= 1, block
        assert block["fd_open"] >= 1, block

    assert (
        payload["resource_after"]["wall_ms"]
        >= payload["resource_before"]["wall_ms"]
    ), payload
