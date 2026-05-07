"""Phase 05 full-filesystem boundary checks for public file ops."""

from __future__ import annotations

import pytest

from .._harness.integrated_cases import (
    assert_committed,
    assert_read,
    emit_metric,
    q,
    summarize_calls,
    timed_call,
)
from .._harness.phase05_public_file_ops import (
    OUTSIDE_SYMLINK_TARGET,
    phase05_call_row,
    phase05_summary_row,
    raw_exists,
    raw_read,
    seed_phase05_imported_base,
    write_phase05_jsonl_artifact,
)
from .._harness.sandbox_fixture import SandboxHandle, WORKSPACE_ROOT


pytestmark = pytest.mark.asyncio


async def test_phase05_full_filesystem_boundary_and_passthrough(
    workspace_base_sandbox: SandboxHandle,
) -> None:
    handle = workspace_base_sandbox
    binding = await seed_phase05_imported_base(handle)
    metrics = []

    relative = await handle.tool.read_file("src/app.py")
    absolute = await handle.tool.read_file(f"{WORKSPACE_ROOT}/src/app.py")
    assert relative.success and absolute.success
    assert relative.exists and absolute.exists
    assert relative.content == absolute.content

    before_escape = await _manifest_version(handle)
    with pytest.raises(RuntimeError, match="escapes workspace"):
        await handle.tool.write_file(
            f"{WORKSPACE_ROOT}/../tmp/phase05-escape.txt",
            "escape\n",
            description="phase05 dotdot escape should fail closed",
        )
    assert await _manifest_version(handle) == before_escape
    assert not await raw_exists(handle, "/tmp/phase05-escape.txt")

    inside_link = await handle.tool.read_file("links/inside")
    assert inside_link.success
    assert inside_link.exists
    assert inside_link.content == relative.content

    outside_link = await handle.tool.read_file("links/outside")
    assert outside_link.success
    assert outside_link.exists
    assert outside_link.content == "outside-base\n"

    before_outside = await _manifest_version(handle)
    outside_write, metric = await timed_call(
        "phase05_fullfs_symlink_outside_write",
        handle.tool.write_file(
            "links/outside",
            "outside-public-write\n",
            description="phase05 write through symlink escape",
        ),
    )
    metrics.append(metric)
    assert_committed(outside_write, path=OUTSIDE_SYMLINK_TARGET)
    assert await _manifest_version(handle) == before_outside
    assert (await raw_read(handle, OUTSIDE_SYMLINK_TARGET)).strip() == (
        "outside-public-write"
    )

    outside_edit, metric = await timed_call(
        "phase05_fullfs_symlink_outside_edit",
        handle.tool.edit_file(
            "links/outside",
            [("outside-public-write", "outside-public-edit")],
            description="phase05 edit through symlink escape",
        ),
    )
    metrics.append(metric)
    assert_committed(outside_edit, path=OUTSIDE_SYMLINK_TARGET)
    assert await _manifest_version(handle) == before_outside
    assert (await raw_read(handle, OUTSIDE_SYMLINK_TARGET)).strip() == (
        "outside-public-edit"
    )

    shell_symlink, metric = await timed_call(
        "phase05_fullfs_shell_symlink_outside",
        handle.tool.shell(
            "printf 'outside-shell\\n' > links/outside",
            timeout=30,
            description="phase05 shell writes through outside symlink",
        ),
    )
    metrics.append(metric)
    assert_committed(shell_symlink)
    assert shell_symlink.changed_paths == ()
    assert await _manifest_version(handle) == before_outside
    assert (await raw_read(handle, OUTSIDE_SYMLINK_TARGET)).strip() == "outside-shell"

    tmp_write, metric = await timed_call(
        "phase05_fullfs_tmp_write",
        handle.tool.write_file(
            "/tmp/phase05-public.txt",
            "tmp-public\n",
            description="phase05 public write outside workspace",
        ),
    )
    metrics.append(metric)
    assert_committed(tmp_write, path="/tmp/phase05-public.txt")
    assert "api.write.occ_apply_s" not in tmp_write.timings
    tmp_read = await handle.tool.read_file("/tmp/phase05-public.txt")
    assert tmp_read.success and tmp_read.exists
    assert tmp_read.content == "tmp-public\n"

    tmp_edit, metric = await timed_call(
        "phase05_fullfs_tmp_edit",
        handle.tool.edit_file(
            "/tmp/phase05-public.txt",
            [("tmp-public", "tmp-edited")],
            description="phase05 public edit outside workspace",
        ),
    )
    metrics.append(metric)
    assert_committed(tmp_edit, path="/tmp/phase05-public.txt")
    assert "api.edit.occ_apply_s" not in tmp_edit.timings
    assert (await raw_read(handle, "/tmp/phase05-public.txt")).strip() == "tmp-edited"

    before_shell_outside = await _manifest_version(handle)
    shell_outside, metric = await timed_call(
        "phase05_fullfs_shell_outside_only",
        handle.tool.shell(
            "set -e; "
            "mkdir -p /root/.cache; "
            "printf 'tmp-shell\\n' > /tmp/phase05-shell.txt; "
            "printf 'cache-shell\\n' > /root/.cache/eos-phase05.txt",
            timeout=30,
            description="phase05 shell outside workspace passthrough",
        ),
    )
    metrics.append(metric)
    assert_committed(shell_outside)
    assert shell_outside.changed_paths == ()
    assert await _manifest_version(handle) == before_shell_outside
    assert (await raw_read(handle, "/tmp/phase05-shell.txt")).strip() == "tmp-shell"
    assert (await raw_read(handle, "/root/.cache/eos-phase05.txt")).strip() == (
        "cache-shell"
    )

    before_mixed = await _manifest_version(handle)
    mixed_shell, metric = await timed_call(
        "phase05_fullfs_shell_workspace_and_outside",
        handle.tool.shell(
            "set -e; "
            f"mkdir -p {q(f'{WORKSPACE_ROOT}/tracked/fullfs')}; "
            f"printf 'absolute workspace\\n' > "
            f"{q(f'{WORKSPACE_ROOT}/tracked/fullfs/absolute.txt')}; "
            "printf 'outside mixed\\n' > /tmp/phase05-mixed-outside.txt",
            timeout=30,
            description="phase05 shell writes /testbed and /tmp",
        ),
    )
    metrics.append(metric)
    assert_committed(mixed_shell, path="tracked/fullfs/absolute.txt")
    assert await _manifest_version(handle) > before_mixed
    await assert_read(handle, "tracked/fullfs/absolute.txt", "absolute workspace\n")
    tmp_after_shell = await handle.tool.read_file("/tmp/phase05-mixed-outside.txt")
    assert tmp_after_shell.success and tmp_after_shell.exists
    assert tmp_after_shell.content == "outside mixed\n"

    summary = phase05_summary_row(
        case="full_filesystem_view",
        binding=binding,
        concurrency=1,
        metrics=metrics,
        batch_wall_ms=sum(metric.elapsed_ms for metric in metrics),
        correctness={
            "relative_and_absolute_workspace_paths_match": True,
            "dotdot_escape_rejected": True,
            "outside_passthrough_no_manifest_advance": True,
            "workspace_shell_publish_manifest_advance": True,
        },
        pass_bars={"symlink_escape_policy": "classify_outside"},
    )
    artifact = write_phase05_jsonl_artifact(
        case="full_filesystem_view",
        rows=[
            summary,
            *(
                phase05_call_row(
                    case="full_filesystem_view",
                    metric=metric,
                    concurrency=1,
                )
                for metric in metrics
            ),
        ],
    )
    emit_metric(
        "phase05.public_file_ops.full_filesystem_view",
        {
            **summarize_calls(metrics),
            "artifact": str(artifact),
        },
    )


async def _manifest_version(handle: SandboxHandle) -> int:
    metrics = await handle.tool.layer_metrics()
    assert metrics["success"] is True
    return int(metrics["manifest_version"])
