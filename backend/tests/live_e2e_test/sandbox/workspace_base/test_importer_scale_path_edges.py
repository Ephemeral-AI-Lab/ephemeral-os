"""Phase-01 importer scale and path edge coverage."""

from __future__ import annotations

import pytest

from .._harness.sandbox_fixture import SandboxHandle
from .._harness.workspace_base_metrics import env_int, write_jsonl_artifact
from .._harness.workspace_base_probe import run_workspace_base_probe


pytestmark = pytest.mark.asyncio


_SCALE_PATH_BODY = r"""
label = "workspace_base.import_scale_path_edges"
case = "base_import_scale_path_edges"
started = time.perf_counter()
cfg = __CFG__
small_file_count = int(cfg["small_file_count"])
large_binary_mib = int(cfg["large_binary_mib"])
seed = WORKSPACE_ROOT / "phase01-scale-path-fixtures"
shutil.rmtree(seed, ignore_errors=True)
seed.mkdir(parents=True, exist_ok=True)

small_root = seed / "small-files"
small_root.mkdir(parents=True, exist_ok=True)
for index in range(small_file_count):
    (small_root / ("%05d.txt" % index)).write_text(
        "small-%05d\n" % index,
        encoding="utf-8",
    )

large = seed / "large-binary.bin"
chunk = bytes((index % 251 for index in range(1024 * 1024)))
with large.open("wb") as file:
    for _ in range(large_binary_mib):
        file.write(chunk)

executable = seed / "bin" / "run-me.sh"
executable.parent.mkdir(parents=True, exist_ok=True)
executable.write_text("#!/bin/sh\nprintf edge\\n\n", encoding="utf-8")
executable.chmod(0o755)

(seed / "target-dir" / "child.txt").parent.mkdir(parents=True, exist_ok=True)
(seed / "target-dir" / "child.txt").write_text("target child\n", encoding="utf-8")
(seed / "symlink-to-dir").symlink_to("target-dir")
(seed / "dangling-link").symlink_to("missing-target")

(seed / "path with spaces.txt").write_text("spaces\n", encoding="utf-8")
(seed / "unicodé-路径.txt").write_text("unicode\n", encoding="utf-8")
long_name = "long-" + ("x" * 140) + ".txt"
(seed / long_name).write_text("long\n", encoding="utf-8")
newline_path = seed / "line\nbreak.txt"
newline_path.write_text("newline\n", encoding="utf-8")
deep_empty = seed / "empty" / "a" / "b" / "c" / "d" / "e"
deep_empty.mkdir(parents=True, exist_ok=True)

workspace_inv = _inventory(WORKSPACE_ROOT)
stack_root = _phase01_root(label)
binding, timings = _build_base(stack_root)
base_layer = stack_root / "layers" / "L000001-base"
base_inv = _inventory(base_layer)

assert workspace_inv["files"] == base_inv["files"], (workspace_inv, base_inv)
assert workspace_inv["dirs"] == base_inv["dirs"], (workspace_inv, base_inv)
assert workspace_inv["symlinks"] == base_inv["symlinks"], (workspace_inv, base_inv)
assert workspace_inv["bytes"] == base_inv["bytes"], (workspace_inv, base_inv)
assert _file_sha(large) == _file_sha(base_layer / "phase01-scale-path-fixtures/large-binary.bin")
assert os.stat(base_layer / "phase01-scale-path-fixtures/bin/run-me.sh").st_mode & 0o111
assert os.readlink(base_layer / "phase01-scale-path-fixtures/symlink-to-dir") == "target-dir"
assert os.readlink(base_layer / "phase01-scale-path-fixtures/dangling-link") == "missing-target"
assert (base_layer / "phase01-scale-path-fixtures/path with spaces.txt").read_text(encoding="utf-8") == "spaces\n"
assert (base_layer / "phase01-scale-path-fixtures/unicodé-路径.txt").read_text(encoding="utf-8") == "unicode\n"
assert (base_layer / "phase01-scale-path-fixtures" / long_name).read_text(encoding="utf-8") == "long\n"
assert (base_layer / "phase01-scale-path-fixtures/line\nbreak.txt").read_text(encoding="utf-8") == "newline\n"
assert (base_layer / "phase01-scale-path-fixtures/empty/a/b/c/d/e").is_dir()
assert len(list((base_layer / "phase01-scale-path-fixtures/small-files").iterdir())) == small_file_count

rows = [
    _call_row(
        case,
        "scale_path_edges",
        True,
        started,
        timings,
        extra={
            "small_file_count": small_file_count,
            "large_binary_mib": large_binary_mib,
            "base_files": base_inv["files"],
            "base_dirs": base_inv["dirs"],
            "base_symlinks": base_inv["symlinks"],
            "base_bytes": base_inv["bytes"],
        },
    )
]
summary = _base_summary(
    case,
    binding,
    workspace_inv,
    timings,
    pass_bars={
        "small_file_count": small_file_count,
        "large_binary_mib": large_binary_mib,
        "executable_bit_preserved": True,
        "dangling_symlink_preserved": True,
        "symlink_to_directory_preserved": True,
        "spaces_unicode_long_and_newline_paths": True,
        "deep_empty_directories": True,
    },
)
_emit_workspace_payload(label, started, summary, rows)
"""


async def test_importer_handles_scale_and_path_edge_fixtures(
    workspace_base_sandbox: SandboxHandle,
) -> None:
    payload = await run_workspace_base_probe(
        workspace_base_sandbox,
        _SCALE_PATH_BODY.replace("__CFG__", "json.loads(__CFG_JSON__)"),
        label="workspace_base.import_scale_path_edges",
        cfg={
            "small_file_count": env_int("EPHEMERALOS_PHASE01_SMALL_FILES", 1000),
            "large_binary_mib": env_int("EPHEMERALOS_PHASE01_LARGE_BINARY_MIB", 32),
        },
        timeout=600,
    )
    rows = payload["rows"]
    assert len(rows) == 1
    assert rows[0]["success"] is True
    artifact = write_jsonl_artifact(
        case="base_import_scale_path_edges",
        summary=payload["summary"],
        rows=rows,
    )
    print(f"\n[phase01:base_import_scale_path_edges] artifact={artifact}")
