"""Runtime-local command-exec server for guarded shell calls."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from sandbox.command_exec import (
    CommandExecResult,
    OCCMutationClient,
    WorkspaceLeaseClient,
    execute_command,
    run_workspace_replaced_command,
)
from sandbox.command_exec.executor import (
    _drop_transient_lowerdir,
    layer_stack_root,
)
from sandbox.occ.content.gitignore_oracle import SnapshotGitignoreOracle
from sandbox.occ.result_projection import (
    conflict_and_status,
    conflict_to_dict,
    published_paths,
)
from sandbox.runtime.daemon.service import occ_backend


async def execute_shell_api(args: dict[str, object]) -> dict[str, object]:
    """Public ``api.shell`` execution entrypoint used by the handler layer."""
    layer_stack, occ_client, gitignore, storage_root = _services(args)
    result = await _execute_shell(
        args,
        layer_stack=layer_stack,
        occ_client=occ_client,
        gitignore=gitignore,
        storage_root=storage_root,
    )
    return _payload_from_result(result)


async def _execute_shell(
    args: Mapping[str, object],
    *,
    layer_stack: WorkspaceLeaseClient,
    occ_client: OCCMutationClient,
    gitignore: SnapshotGitignoreOracle,
    storage_root: Path,
) -> CommandExecResult:
    return await execute_command(
        args,
        layer_stack=layer_stack,
        occ_client=occ_client,
        gitignore=gitignore,
        storage_root=storage_root,
        command_runner=run_workspace_replaced_command,
    )


def _payload_from_result(result: CommandExecResult) -> dict[str, object]:
    changeset = result.occ_result
    files = getattr(changeset, "files", ())
    conflict, conflict_status = conflict_and_status(files)
    command_failed = result.exit_code != 0
    success = not command_failed and bool(getattr(changeset, "success", False))
    status = "ok" if success else conflict_status if conflict is not None else "error"
    return {
        "success": success,
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "changed_paths": list(published_paths(files)),
        "status": status,
        "conflict": conflict_to_dict(conflict),
        "conflict_reason": conflict.message if conflict is not None else None,
        "workspace_capture": {
            "snapshot_version": result.workspace_capture.snapshot_version,
            "mount_mode": result.workspace_capture.mount_mode,
            "changes": [
                change.to_dict() if hasattr(change, "to_dict") else str(change)
                for change in result.workspace_capture.changes
            ],
        },
        "warnings": [],
        "timings": result.timings,
    }


def _services(
    args: Mapping[str, object],
) -> tuple[
    WorkspaceLeaseClient,
    OCCMutationClient,
    SnapshotGitignoreOracle,
    Path,
]:
    backend = occ_backend.build_occ_backend(layer_stack_root(args))
    return (
        backend.layer_stack,
        backend.occ_client,
        backend.gitignore,
        backend.layer_stack.storage_root,
    )
