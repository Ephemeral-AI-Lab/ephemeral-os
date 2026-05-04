"""Shell merge edge-case load suites for the sandbox API."""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from _load_helpers import (
    ApiLoadEnv,
    _BarrierOccService,
    LoadRecorder,
    _BarrierInvoker,
    _Gitignore,
    _assert_all_success,
    _assert_logged_progress,
    _assert_single_winner,
    _assert_timing_keys,
    _compact_stack,
    _run_load_batch,
    api_load_env as _shared_api_load_env,
)
from sandbox.api import EditFileRequest, SearchReplaceEdit, ShellRequest
from sandbox.api.edit import edit_file
from sandbox.api.shell import shell
from sandbox.layer_stack import LayerChange
from sandbox.occ.client import register_occ_service
from sandbox.occ.service import OccService
from sandbox.overlay.client import OverlayClient, register_overlay_client
from sandbox.overlay.runner.snapshot_overlay_runner import SnapshotOverlayRunner


api_load_env = _shared_api_load_env
EDGE_CONCURRENCY_LEVELS = (3, 5)
EDGE_TIMING_KEYS = (
    "api.shell.total_s",
    "overlay.run_command_s",
    "overlay.capture_changes_s",
    "occ.commit.total_s",
    "occ.serial.batch_size",
)
EDIT_EDGE_TIMING_KEYS = (
    "api.edit.total_s",
    "api.edit.occ_apply_s",
    "occ.prepare.total_s",
    "occ.commit.total_s",
    "occ.commit.publish_layer_s",
    "test.occ.prepare_barrier_wait_s",
)


async def test_shell_delete_merge_success_and_conflict_levels_3_5(
    request: pytest.FixtureRequest,
) -> None:
    api_load_env: ApiLoadEnv = request.getfixturevalue("api_load_env")
    recorder = LoadRecorder("shell_delete_merge_edges")
    seen = set()
    for level in EDGE_CONCURRENCY_LEVELS:
        _register_barrier_overlay(api_load_env, parties=level)
        for index in range(level):
            api_load_env.seed(
                f"edge/delete/disjoint/{level}/{index}.txt",
                f"delete-me-{level}-{index}\n",
            )

        async def delete_disjoint(index: int):
            path = f"edge/delete/disjoint/{level}/{index}.txt"
            return await shell(
                api_load_env.sandbox_id,
                ShellRequest(
                    command=_delete_command(path),
                    actor=api_load_env.actor(index),
                    timeout=20,
                    description="delete disjoint paths",
                ),
            )

        report = await _run_load_batch(
            api_load_env,
            recorder,
            label="delete_disjoint",
            concurrency=level,
            operation=delete_disjoint,
        )
        _assert_all_success(report)
        _assert_timing_keys(report, EDGE_TIMING_KEYS)
        for index in range(level):
            assert api_load_env.manager.read_text(
                f"edge/delete/disjoint/{level}/{index}.txt"
            ) == ("", False)
        _compact_stack(api_load_env)

        shared_path = f"edge/delete/conflict/{level}/shared.txt"
        api_load_env.seed(shared_path, "delete-shared\n")
        _register_barrier_overlay(api_load_env, parties=level)

        async def delete_shared(index: int):
            del index
            return await shell(
                api_load_env.sandbox_id,
                ShellRequest(
                    command=_delete_command(shared_path),
                    actor=api_load_env.actor(100 + level),
                    timeout=20,
                    description="delete same path conflict",
                ),
            )

        conflict_report = await _run_load_batch(
            api_load_env,
            recorder,
            label="delete_conflict",
            concurrency=level,
            operation=delete_shared,
        )
        _assert_single_winner(conflict_report, conflict_status="aborted_version")
        _assert_timing_keys(conflict_report, EDGE_TIMING_KEYS)
        assert api_load_env.manager.read_text(shared_path) == ("", False)
        _compact_stack(api_load_env)
        seen.add(level)

    assert seen == set(EDGE_CONCURRENCY_LEVELS)
    _assert_logged_progress(recorder)


async def test_shell_rename_merge_success_and_conflict_levels_3_5(
    request: pytest.FixtureRequest,
) -> None:
    api_load_env: ApiLoadEnv = request.getfixturevalue("api_load_env")
    recorder = LoadRecorder("shell_rename_merge_edges")
    seen = set()
    for level in EDGE_CONCURRENCY_LEVELS:
        _register_barrier_overlay(api_load_env, parties=level)
        for index in range(level):
            api_load_env.seed(
                f"edge/rename/disjoint/{level}/src-{index}.txt",
                f"rename-{level}-{index}\n",
            )

        async def rename_disjoint(index: int):
            src = f"edge/rename/disjoint/{level}/src-{index}.txt"
            dst = f"edge/rename/disjoint/{level}/dst/renamed-{index}.txt"
            return await shell(
                api_load_env.sandbox_id,
                ShellRequest(
                    command=_rename_command(src, dst),
                    actor=api_load_env.actor(index),
                    timeout=20,
                    description="rename disjoint paths",
                ),
            )

        report = await _run_load_batch(
            api_load_env,
            recorder,
            label="rename_disjoint",
            concurrency=level,
            operation=rename_disjoint,
        )
        _assert_all_success(report)
        _assert_timing_keys(report, EDGE_TIMING_KEYS)
        for index in range(level):
            assert api_load_env.manager.read_text(
                f"edge/rename/disjoint/{level}/src-{index}.txt"
            ) == ("", False)
            assert api_load_env.manager.read_text(
                f"edge/rename/disjoint/{level}/dst/renamed-{index}.txt"
            ) == (f"rename-{level}-{index}\n", True)
        _compact_stack(api_load_env)

        shared_src = f"edge/rename/conflict/{level}/shared.txt"
        api_load_env.seed(shared_src, "rename-shared\n")
        _register_barrier_overlay(api_load_env, parties=level)

        async def rename_shared(index: int):
            dst = f"edge/rename/conflict/{level}/dst/renamed-{index}.txt"
            return await shell(
                api_load_env.sandbox_id,
                ShellRequest(
                    command=_rename_command(shared_src, dst),
                    actor=api_load_env.actor(index),
                    timeout=20,
                    description="rename same source conflict",
                ),
            )

        conflict_report = await _run_load_batch(
            api_load_env,
            recorder,
            label="rename_conflict",
            concurrency=level,
            operation=rename_shared,
        )
        _assert_single_winner(conflict_report, conflict_status="aborted_version")
        _assert_timing_keys(conflict_report, EDGE_TIMING_KEYS)
        assert api_load_env.manager.read_text(shared_src) == ("", False)
        created = [
            index
            for index in range(level)
            if api_load_env.manager.read_text(
                f"edge/rename/conflict/{level}/dst/renamed-{index}.txt"
            )[1]
        ]
        assert len(created) == 1
        assert api_load_env.manager.read_text(
            f"edge/rename/conflict/{level}/dst/renamed-{created[0]}.txt"
        ) == ("rename-shared\n", True)
        _compact_stack(api_load_env)
        seen.add(level)

    assert seen == set(EDGE_CONCURRENCY_LEVELS)
    _assert_logged_progress(recorder)


async def test_shell_dir_file_replacement_merge_success_levels_3_5(
    request: pytest.FixtureRequest,
) -> None:
    api_load_env: ApiLoadEnv = request.getfixturevalue("api_load_env")
    recorder = LoadRecorder("shell_dir_file_replacement_edges")
    seen = set()
    for level in EDGE_CONCURRENCY_LEVELS:
        _register_barrier_overlay(api_load_env, parties=level)
        for index in range(level):
            api_load_env.seed(
                f"edge/dir-file/file-to-dir/{level}/{index}",
                f"old-file-{index}\n",
            )
            api_load_env.seed(
                f"edge/dir-file/dir-to-file/{level}/{index}/child.txt",
                f"old-child-{index}\n",
            )

        async def file_to_dir(index: int):
            path = f"edge/dir-file/file-to-dir/{level}/{index}"
            return await shell(
                api_load_env.sandbox_id,
                ShellRequest(
                    command=_file_to_dir_command(
                        path,
                        child="child.txt",
                        payload=f"new-child-{level}-{index}\n",
                    ),
                    actor=api_load_env.actor(index),
                    timeout=20,
                    description="replace file with directory",
                ),
            )

        file_to_dir_report = await _run_load_batch(
            api_load_env,
            recorder,
            label="file_to_dir",
            concurrency=level,
            operation=file_to_dir,
        )
        _assert_all_success(file_to_dir_report)
        _assert_timing_keys(file_to_dir_report, EDGE_TIMING_KEYS)
        for index in range(level):
            path = f"edge/dir-file/file-to-dir/{level}/{index}"
            assert api_load_env.manager.read_text(path) == ("", False)
            assert api_load_env.manager.read_text(f"{path}/child.txt") == (
                f"new-child-{level}-{index}\n",
                True,
            )
            assert api_load_env.manager.list_dir(path) == ("child.txt",)
        _compact_stack(api_load_env)

        _register_barrier_overlay(api_load_env, parties=level)

        async def dir_to_file(index: int):
            path = f"edge/dir-file/dir-to-file/{level}/{index}"
            return await shell(
                api_load_env.sandbox_id,
                ShellRequest(
                    command=_dir_to_file_command(
                        path,
                        payload=f"new-file-{level}-{index}\n",
                    ),
                    actor=api_load_env.actor(index),
                    timeout=20,
                    description="replace directory with file",
                ),
            )

        dir_to_file_report = await _run_load_batch(
            api_load_env,
            recorder,
            label="dir_to_file",
            concurrency=level,
            operation=dir_to_file,
        )
        _assert_all_success(dir_to_file_report)
        _assert_timing_keys(dir_to_file_report, EDGE_TIMING_KEYS)
        for index in range(level):
            path = f"edge/dir-file/dir-to-file/{level}/{index}"
            assert api_load_env.manager.read_text(path) == (
                f"new-file-{level}-{index}\n",
                True,
            )
            assert api_load_env.manager.read_text(f"{path}/child.txt") == ("", False)
            assert api_load_env.manager.list_dir(path) == ()
        _compact_stack(api_load_env)
        seen.add(level)

    assert seen == set(EDGE_CONCURRENCY_LEVELS)
    _assert_logged_progress(recorder)


async def test_shell_symlink_mode_change_merge_success_levels_3_5(
    request: pytest.FixtureRequest,
) -> None:
    api_load_env: ApiLoadEnv = request.getfixturevalue("api_load_env")
    recorder = LoadRecorder("shell_symlink_mode_edges")
    seen = set()
    for level in EDGE_CONCURRENCY_LEVELS:
        _register_barrier_overlay(api_load_env, parties=level)
        for index in range(level):
            api_load_env.seed(
                f"edge/symlink/file-to-link/{level}/{index}.txt",
                f"old-file-{index}\n",
            )
            _seed_symlink(
                api_load_env,
                f"edge/symlink/link-to-file/{level}/{index}.lnk",
                f"old-target-{index}",
            )

        async def file_to_symlink(index: int):
            path = f"edge/symlink/file-to-link/{level}/{index}.txt"
            return await shell(
                api_load_env.sandbox_id,
                ShellRequest(
                    command=_file_to_symlink_command(path, target=f"target-{index}"),
                    actor=api_load_env.actor(index),
                    timeout=20,
                    description="replace file with symlink",
                ),
            )

        file_to_symlink_report = await _run_load_batch(
            api_load_env,
            recorder,
            label="file_to_symlink",
            concurrency=level,
            operation=file_to_symlink,
        )
        _assert_all_success(file_to_symlink_report)
        _assert_timing_keys(file_to_symlink_report, EDGE_TIMING_KEYS)
        for index in range(level):
            assert api_load_env.manager.read_symlink(
                f"edge/symlink/file-to-link/{level}/{index}.txt"
            ) == (f"target-{index}", True)
        _compact_stack(api_load_env)

        _register_barrier_overlay(api_load_env, parties=level)

        async def symlink_to_file(index: int):
            path = f"edge/symlink/link-to-file/{level}/{index}.lnk"
            return await shell(
                api_load_env.sandbox_id,
                ShellRequest(
                    command=_symlink_to_file_command(
                        path,
                        payload=f"new-file-{level}-{index}\n",
                    ),
                    actor=api_load_env.actor(index),
                    timeout=20,
                    description="replace symlink with file",
                ),
            )

        symlink_to_file_report = await _run_load_batch(
            api_load_env,
            recorder,
            label="symlink_to_file",
            concurrency=level,
            operation=symlink_to_file,
        )
        _assert_all_success(symlink_to_file_report)
        _assert_timing_keys(symlink_to_file_report, EDGE_TIMING_KEYS)
        for index in range(level):
            path = f"edge/symlink/link-to-file/{level}/{index}.lnk"
            assert api_load_env.manager.read_symlink(path) == ("", False)
            assert api_load_env.manager.read_text(path) == (
                f"new-file-{level}-{index}\n",
                True,
            )
        _compact_stack(api_load_env)
        seen.add(level)

    assert seen == set(EDGE_CONCURRENCY_LEVELS)
    _assert_logged_progress(recorder)


async def test_shell_package_lock_same_file_updates_conflict_levels_3_5(
    request: pytest.FixtureRequest,
) -> None:
    api_load_env: ApiLoadEnv = request.getfixturevalue("api_load_env")
    recorder = LoadRecorder("shell_package_lock_same_file_edges")
    seen = set()
    for level in EDGE_CONCURRENCY_LEVELS:
        path = f"edge/package-lock/{level}/package-lock.json"
        api_load_env.seed(path, _package_lock_payload())
        _register_barrier_overlay(api_load_env, parties=level)

        async def op(index: int):
            payload = _package_lock_payload(package=f"pkg-{index}", version=f"1.0.{index}")
            return await shell(
                api_load_env.sandbox_id,
                ShellRequest(
                    command=_write_text_command(path, payload),
                    actor=api_load_env.actor(index),
                    timeout=20,
                    description="package-lock same-file update conflict",
                ),
            )

        report = await _run_load_batch(
            api_load_env,
            recorder,
            label="package_lock_conflict",
            concurrency=level,
            operation=op,
        )
        _assert_single_winner(report, conflict_status="aborted_version")
        _assert_timing_keys(report, EDGE_TIMING_KEYS)
        content, exists = api_load_env.manager.read_text(path)
        assert exists is True
        parsed = json.loads(content)
        dependencies = parsed["packages"][""]["dependencies"]
        assert len(dependencies) == 1
        assert set(dependencies) <= {f"pkg-{index}" for index in range(level)}
        _compact_stack(api_load_env)
        seen.add(level)

    assert seen == set(EDGE_CONCURRENCY_LEVELS)
    _assert_logged_progress(recorder)


async def test_edit_package_lock_same_file_dependency_merge_levels_3_5(
    request: pytest.FixtureRequest,
) -> None:
    api_load_env: ApiLoadEnv = request.getfixturevalue("api_load_env")
    recorder = LoadRecorder("edit_package_lock_same_file_merge_edges")
    seen = set()
    for level in EDGE_CONCURRENCY_LEVELS:
        register_occ_service(
            api_load_env.sandbox_id,
            _BarrierOccService(
                OccService(gitignore=_Gitignore(), layer_stack=api_load_env.manager),
                layer_stack=api_load_env.manager,
                parties=level,
            ),
        )
        path = f"edge/package-lock-merge/{level}/package-lock.json"
        api_load_env.seed(path, _package_lock_merge_payload(level))

        async def op(index: int):
            return await edit_file(
                api_load_env.sandbox_id,
                EditFileRequest(
                    path=path,
                    edits=_package_lock_dependency_edits(index),
                    actor=api_load_env.actor(index),
                    description="package-lock same-file dependency merge",
                ),
            )

        report = await _run_load_batch(
            api_load_env,
            recorder,
            label="package_lock_dependency_merge",
            concurrency=level,
            operation=op,
        )
        _assert_all_success(report)
        _assert_timing_keys(report, EDIT_EDGE_TIMING_KEYS)
        content, exists = api_load_env.manager.read_text(path)
        assert exists is True
        parsed = json.loads(content)
        dependencies = parsed["packages"][""]["dependencies"]
        assert dependencies == {
            f"pkg-{index}": f"1.0.{index}" for index in range(level)
        }
        package_names = set(parsed["packages"])
        assert not any("__slot_" in name for name in package_names)
        assert {
            f"node_modules/pkg-{index}" for index in range(level)
        }.issubset(package_names)
        _compact_stack(api_load_env)
        seen.add(level)

    assert seen == set(EDGE_CONCURRENCY_LEVELS)
    _assert_logged_progress(recorder)


def _register_barrier_overlay(env: ApiLoadEnv, *, parties: int) -> None:
    register_overlay_client(
        env.sandbox_id,
        OverlayClient(
            runner=SnapshotOverlayRunner(
                env.manager,
                invoker=_BarrierInvoker(
                    storage_root=env.manager.storage_root,
                    parties=parties,
                ),
            )
        ),
    )


def _seed_symlink(env: ApiLoadEnv, path: str, target: str) -> None:
    env.manager.publish_changes((LayerChange(path=path, kind="symlink", source_path=target),))


def _delete_command(path: str) -> str:
    return f"sleep 0.05; rm -f {shlex.quote(path)}"


def _rename_command(src: str, dst: str) -> str:
    return (
        f"mkdir -p {shlex.quote(str(Path(dst).parent))}; "
        f"sleep 0.05; mv {shlex.quote(src)} {shlex.quote(dst)}"
    )


def _file_to_dir_command(path: str, *, child: str, payload: str) -> str:
    child_path = f"{path}/{child}"
    return (
        f"rm -f {shlex.quote(path)}; "
        f"mkdir -p {shlex.quote(path)}; "
        f"sleep 0.05; "
        f"printf {shlex.quote(payload)} > {shlex.quote(child_path)}"
    )


def _dir_to_file_command(path: str, *, payload: str) -> str:
    return f"rm -rf {shlex.quote(path)}; sleep 0.05; printf {shlex.quote(payload)} > {shlex.quote(path)}"


def _file_to_symlink_command(path: str, *, target: str) -> str:
    return f"rm -f {shlex.quote(path)}; sleep 0.05; ln -s {shlex.quote(target)} {shlex.quote(path)}"


def _symlink_to_file_command(path: str, *, payload: str) -> str:
    return f"rm -f {shlex.quote(path)}; sleep 0.05; printf {shlex.quote(payload)} > {shlex.quote(path)}"


def _write_text_command(path: str, payload: str) -> str:
    parent = shlex.quote(str(Path(path).parent))
    quoted_path = shlex.quote(path)
    return f"mkdir -p {parent}; sleep 0.05; cat > {quoted_path} <<'EOS_TEXT'\n{payload}\nEOS_TEXT\n"


def _package_lock_payload(package: str | None = None, version: str = "1.0.0") -> str:
    dependencies = {} if package is None else {package: version}
    packages = {"": {"dependencies": dependencies}}
    if package is not None:
        packages[f"node_modules/{package}"] = {"version": version}
    return json.dumps(
        {
            "name": "edge-lock",
            "lockfileVersion": 3,
            "requires": True,
            "packages": packages,
        },
        indent=2,
        sort_keys=True,
    )


def _package_lock_merge_payload(slots: int) -> str:
    packages = {
        "": {
            "dependencies": {
                f"__slot_{index}__": "0.0.0" for index in range(slots)
            }
        }
    }
    for index in range(slots):
        packages[f"node_modules/__slot_{index}__"] = {"version": "0.0.0"}
    return json.dumps(
        {
            "name": "edge-lock",
            "lockfileVersion": 3,
            "requires": True,
            "packages": packages,
        },
        indent=2,
        sort_keys=True,
    )


def _package_lock_dependency_edits(index: int) -> tuple[SearchReplaceEdit, ...]:
    return (
        SearchReplaceEdit(
            old_text=f'"__slot_{index}__": "0.0.0"',
            new_text=f'"pkg-{index}": "1.0.{index}"',
        ),
        SearchReplaceEdit(
            old_text=(
                f'"node_modules/__slot_{index}__": {{\n'
                '      "version": "0.0.0"\n'
                "    }"
            ),
            new_text=(
                f'"node_modules/pkg-{index}": {{\n'
                f'      "version": "1.0.{index}"\n'
                "    }"
            ),
        ),
    )
