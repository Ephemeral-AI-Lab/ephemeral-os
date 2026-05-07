"""Phase 05 public file-op correctness over an imported `/testbed` base."""

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
    phase05_call_row,
    phase05_summary_row,
    public_reconcile,
    seed_phase05_imported_base,
    write_phase05_jsonl_artifact,
)
from .._harness.sandbox_fixture import SandboxHandle, WORKSPACE_ROOT


pytestmark = pytest.mark.asyncio


async def test_phase05_public_view_uses_imported_base_and_committed_layers(
    workspace_base_sandbox: SandboxHandle,
) -> None:
    handle = workspace_base_sandbox
    binding = await seed_phase05_imported_base(handle)
    metrics = []

    read_app, metric = await timed_call(
        "phase05_correctness_read_app",
        handle.tool.read_file("src/app.py"),
    )
    metrics.append(metric)
    assert read_app.success
    assert read_app.exists
    assert "base-app" in read_app.content

    read_settings, metric = await timed_call(
        "phase05_correctness_read_settings_abs",
        handle.tool.read_file(f"{WORKSPACE_ROOT}/src/config/settings.json"),
    )
    metrics.append(metric)
    assert read_settings.success
    assert read_settings.exists
    assert '"mode": "base"' in read_settings.content

    shell_base, metric = await timed_call(
        "phase05_correctness_shell_reads_base",
        handle.tool.shell(
            "set -e; cat README.md; cat src/app.py",
            timeout=30,
            description="phase05 shell reads imported base",
        ),
    )
    metrics.append(metric)
    assert_committed(shell_base)
    assert "Phase 05 Base" in shell_base.stdout
    assert "base-app" in shell_base.stdout
    assert shell_base.changed_paths == ()

    raw_mutation = await handle.raw_exec(
        handle.sandbox_id,
        f"printf 'dirty\\n' > {q(f'{WORKSPACE_ROOT}/raw.txt')}",
        timeout=30,
    )
    assert raw_mutation.exit_code == 0, raw_mutation.stderr or raw_mutation.stdout

    raw_read, metric = await timed_call(
        "phase05_correctness_read_after_raw_mutation",
        handle.tool.read_file("raw.txt"),
    )
    metrics.append(metric)
    assert raw_read.success
    assert raw_read.exists
    assert raw_read.content == "base\n"

    raw_shell, metric = await timed_call(
        "phase05_correctness_shell_after_raw_mutation",
        handle.tool.shell(
            "cat raw.txt",
            timeout=30,
            description="phase05 shell ignores raw workspace mutation",
        ),
    )
    metrics.append(metric)
    assert_committed(raw_shell)
    assert raw_shell.stdout == "base\n"

    write, metric = await timed_call(
        "phase05_correctness_write_tracked",
        handle.tool.write_file(
            "tracked/new-public-write.txt",
            "from public write\n",
            description="phase05 correctness public write",
        ),
    )
    metrics.append(metric)
    assert_committed(write, path="tracked/new-public-write.txt")

    edit, metric = await timed_call(
        "phase05_correctness_edit_base",
        handle.tool.edit_file(
            "tracked/edit-target.txt",
            [("alpha=old", "alpha=new")],
            description="phase05 correctness public edit",
        ),
    )
    metrics.append(metric)
    assert_committed(edit, path="tracked/edit-target.txt")
    assert edit.applied_edits == 1

    shell_write, metric = await timed_call(
        "phase05_correctness_shell_write_tracked_and_ignored",
        handle.tool.shell(
            "set -e; "
            "mkdir -p tracked/shell dist; "
            "printf 'from shell tracked\\n' > tracked/shell/output.txt; "
            "printf 'from shell ignored\\n' > dist/phase05-ignored.txt",
            timeout=30,
            description="phase05 shell writes tracked and ignored paths",
        ),
    )
    metrics.append(metric)
    assert_committed(shell_write, path="tracked/shell/output.txt")

    await public_reconcile(
        handle,
        {
            "raw.txt": "base\n",
            "tracked/new-public-write.txt": "from public write\n",
            "tracked/edit-target.txt": "alpha=new\nbeta=stable\ngamma=old\n",
            "tracked/shell/output.txt": "from shell tracked\n",
            "dist/phase05-ignored.txt": "from shell ignored\n",
        },
    )

    shell_view = await handle.tool.shell(
        "set -e; cat tracked/new-public-write.txt; cat tracked/edit-target.txt",
        timeout=30,
        description="phase05 shell sees committed layer view",
    )
    assert_committed(shell_view)
    assert "from public write" in shell_view.stdout
    assert "alpha=new" in shell_view.stdout

    summary = phase05_summary_row(
        case="correctness",
        binding=binding,
        concurrency=1,
        metrics=metrics,
        batch_wall_ms=sum(metric.elapsed_ms for metric in metrics),
        correctness={
            "raw_workspace_mutation_isolated": True,
            "public_read_reconciliation": True,
            "public_shell_reconciliation": True,
        },
        pass_bars={"public_verbs": ["read_file", "write_file", "edit_file", "shell"]},
    )
    artifact = write_phase05_jsonl_artifact(
        case="correctness",
        rows=[
            summary,
            *(
                phase05_call_row(
                    case="correctness",
                    metric=metric,
                    concurrency=1,
                )
                for metric in metrics
            ),
        ],
    )
    emit_metric(
        "phase05.public_file_ops.correctness",
        {
            **summarize_calls(metrics),
            "artifact": str(artifact),
        },
    )


async def test_phase05_public_missing_reads_have_absent_shape(
    workspace_base_sandbox: SandboxHandle,
) -> None:
    handle = workspace_base_sandbox
    await seed_phase05_imported_base(handle)

    missing_workspace = await handle.tool.read_file("tracked/missing.txt")
    assert missing_workspace.success
    assert not missing_workspace.exists
    assert missing_workspace.content == ""

    missing_outside = await handle.tool.read_file("/tmp/eos-phase05-missing.txt")
    assert missing_outside.success
    assert not missing_outside.exists
    assert missing_outside.content == ""

    still_base = await assert_read(handle, "raw.txt", "base\n")
    assert still_base.exists
