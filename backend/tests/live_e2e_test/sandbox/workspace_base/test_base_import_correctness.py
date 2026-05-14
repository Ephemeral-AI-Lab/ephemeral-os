"""Phase-01 correctness checks for importing real workspace base content."""

from __future__ import annotations

import pytest

from .._harness.sandbox_fixture import SandboxHandle
from .._harness.workspace_base_metrics import env_flag, write_jsonl_artifact
from .._harness.workspace_base_probe import run_workspace_base_probe


pytestmark = pytest.mark.asyncio


_CORRECTNESS_BODY = r"""
label = "workspace_base.import_correctness"
case = "base_import_correctness"
started = time.perf_counter()
seed = WORKSPACE_ROOT / "phase01-correctness-fixtures"
shutil.rmtree(seed, ignore_errors=True)
seed.mkdir(parents=True, exist_ok=True)
(seed / "empty-dir").mkdir()
(seed / "unicode-path-π.txt").write_text("unicode\n", encoding="utf-8")
long_dir = seed / ("long-" + ("x" * 120))
long_dir.mkdir(parents=True, exist_ok=True)
(long_dir / "leaf.txt").write_text("long\n", encoding="utf-8")
binary = seed / "binary.bin"
binary.write_bytes(bytes(range(256)) * 8)
(seed / "symlink-target.txt").write_text("target\n", encoding="utf-8")
symlink = seed / "symlink-link.txt"
try:
    symlink.symlink_to("symlink-target.txt")
except FileExistsError:
    pass

stack_root = _phase01_root(label)
binding, timings = _build_base(stack_root)
manager = LayerStackManager(stack_root)
base_layer = stack_root / "layers" / "B000001-base"
raw_inventory = _inventory(WORKSPACE_ROOT)
base_inventory = _inventory(base_layer)

assert raw_inventory["files"] == base_inventory["files"], (raw_inventory, base_inventory)
assert raw_inventory["dirs"] == base_inventory["dirs"], (raw_inventory, base_inventory)
assert raw_inventory["symlinks"] == base_inventory["symlinks"], (raw_inventory, base_inventory)
assert raw_inventory["bytes"] == base_inventory["bytes"], (raw_inventory, base_inventory)
assert _file_sha(binary) == _file_sha(base_layer / "phase01-correctness-fixtures/binary.bin")
assert os.readlink(base_layer / "phase01-correctness-fixtures/symlink-link.txt") == "symlink-target.txt"
assert (base_layer / "phase01-correctness-fixtures/empty-dir").is_dir()
assert (base_layer / "phase01-correctness-fixtures/unicode-path-π.txt").read_text(encoding="utf-8") == "unicode\n"
assert (base_layer / "phase01-correctness-fixtures" / long_dir.name / "leaf.txt").read_text(encoding="utf-8") == "long\n"
assert manager.read_bytes("phase01-correctness-fixtures/binary.bin")[1] is True
assert manager.read_symlink("phase01-correctness-fixtures/symlink-link.txt") == ("symlink-target.txt", True)
assert manager.list_dir("phase01-correctness-fixtures/empty-dir") == ()
assert "gitignore" not in json.dumps(binding).lower()
assert "classification" not in json.dumps(binding).lower()
assert not (base_layer / ".layer-metadata").exists()

summary = _base_summary(
    case,
    binding,
    raw_inventory,
    timings,
    pass_bars={
        "file_count_matches": True,
        "dir_count_matches": True,
        "symlink_round_trip": True,
        "binary_hash_round_trip": True,
        "empty_dirs_round_trip": True,
        "unicode_and_long_paths_round_trip": True,
        "full_inventory": bool(__CFG__["full_inventory"]),
    },
)
rows = [
    _call_row(
        case,
        "base_layer_inventory",
        True,
        started,
        timings,
        extra={
            "base_layer_files": base_inventory["files"],
            "base_layer_dirs": base_inventory["dirs"],
            "base_layer_symlinks": base_inventory["symlinks"],
        },
    )
]
_emit_workspace_payload(
    label,
    started,
    summary,
    rows,
    extra={
        "raw_inventory": raw_inventory,
        "base_inventory": base_inventory,
    },
)
"""


async def test_base_import_preserves_representable_workspace_inventory(
    workspace_base_sandbox: SandboxHandle,
) -> None:
    payload = await run_workspace_base_probe(
        workspace_base_sandbox,
        _CORRECTNESS_BODY.replace("__CFG__", "json.loads(__CFG_JSON__)"),
        label="workspace_base.import_correctness",
        cfg={"full_inventory": env_flag("EPHEMERALOS_PHASE01_FULL_INVENTORY")},
        timeout=240,
    )
    raw_inventory = payload["raw_inventory"]
    base_inventory = payload["base_inventory"]
    assert raw_inventory["files"] == base_inventory["files"]
    assert raw_inventory["dirs"] == base_inventory["dirs"]
    assert raw_inventory["symlinks"] == base_inventory["symlinks"]
    artifact = write_jsonl_artifact(
        case="base_import_correctness",
        summary=payload["summary"],
        rows=payload["rows"],
    )
    print(f"\n[phase01:base_import_correctness] artifact={artifact}")
