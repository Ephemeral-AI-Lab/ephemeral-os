"""Shell package-install load suites for the sandbox API.

The suite uses generated local package fixtures so the load behavior exercises
pip/npm install file churn without relying on external package registries.
"""

from __future__ import annotations

import io
import json
import shlex
import shutil
import subprocess
import sys
import tarfile
import uuid
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from _load_helpers import (
    ApiLoadEnv,
    LoadRecorder,
    _assert_all_success,
    _assert_logged_progress,
    _assert_timing_keys,
    _compact_stack,
    _run_load_batch,
    api_load_env as _shared_api_load_env,
)
from sandbox.api import ShellRequest
from sandbox.api.shell import shell
from sandbox.layer_stack import LayerChange
from sandbox.occ.content.hashing import ContentHasher


api_load_env = _shared_api_load_env
INSTALL_CONCURRENCY_LEVELS = (3, 5)
INSTALL_PACKAGE_COUNT = 10
INSTALL_FILES_PER_PACKAGE = 12
SHELL_INSTALL_TIMING_KEYS = (
    "api.shell.total_s",
    "overlay.run_command_s",
    "overlay.capture_changes_s",
    "occ.commit.total_s",
    "occ.serial.batch_size",
)


async def test_shell_pip_install_load_levels_3_5(
    request: pytest.FixtureRequest,
) -> None:
    api_load_env: ApiLoadEnv = request.getfixturevalue("api_load_env")
    _require_pip()
    recorder = LoadRecorder("shell_pip_install_load")
    package_dirs = _seed_pip_install_fixture(api_load_env)
    recorder.emit(
        "pip_install_fixture_seeded",
        packages=len(package_dirs),
        files_per_package=INSTALL_FILES_PER_PACKAGE,
        layer_stack=api_load_env.layer_stack_metrics(),
    )

    seen = set()
    for level in INSTALL_CONCURRENCY_LEVELS:

        async def op(index: int):
            target = f"load/install/pip/{level}/{index}/target"
            return await shell(
                api_load_env.sandbox_id,
                ShellRequest(
                    command=_pip_install_command(package_dirs, target=target),
                    actor=api_load_env.actor(index),
                    timeout=120,
                    description="pip install load",
                ),
            )

        report = await _run_load_batch(
            api_load_env,
            recorder,
            label="pip_install",
            concurrency=level,
            operation=op,
        )
        _assert_all_success(report)
        _assert_timing_keys(report, SHELL_INSTALL_TIMING_KEYS)
        for index in range(level):
            assert api_load_env.manager.read_text(
                f"load/install/pip/{level}/{index}/target/eos_load_pip_pkg_0/__init__.py"
            ) == ("PACKAGE_INDEX = 0\n", True)
        _compact_stack(api_load_env)
        seen.add(level)

    assert seen == set(INSTALL_CONCURRENCY_LEVELS)
    _assert_logged_progress(recorder)


async def test_shell_npm_install_load_levels_3_5(
    request: pytest.FixtureRequest,
) -> None:
    api_load_env: ApiLoadEnv = request.getfixturevalue("api_load_env")
    npm_path = _require_npm()
    recorder = LoadRecorder("shell_npm_install_load")
    tarballs = _seed_npm_install_fixture(api_load_env)
    recorder.emit(
        "npm_install_fixture_seeded",
        packages=len(tarballs),
        files_per_package=INSTALL_FILES_PER_PACKAGE,
        layer_stack=api_load_env.layer_stack_metrics(),
    )

    seen = set()
    for level in INSTALL_CONCURRENCY_LEVELS:

        async def op(index: int):
            project = f"load/install/npm/{level}/{index}/project"
            cache = f"load/install/npm/{level}/{index}/cache"
            return await shell(
                api_load_env.sandbox_id,
                ShellRequest(
                    command=_npm_install_command(
                        tarballs,
                        npm_path=npm_path,
                        project=project,
                        cache=cache,
                    ),
                    actor=api_load_env.actor(index),
                    timeout=120,
                    description="npm install load",
                ),
            )

        report = await _run_load_batch(
            api_load_env,
            recorder,
            label="npm_install",
            concurrency=level,
            operation=op,
        )
        _assert_all_success(report)
        _assert_timing_keys(report, SHELL_INSTALL_TIMING_KEYS)
        for index in range(level):
            assert api_load_env.manager.read_text(
                f"load/install/npm/{level}/{index}/project/node_modules/"
                "eos-load-npm-pkg-0/index.js"
            ) == ("module.exports = { packageIndex: 0 };\n", True)
        _compact_stack(api_load_env)
        seen.add(level)

    assert seen == set(INSTALL_CONCURRENCY_LEVELS)
    _assert_logged_progress(recorder)


def _require_pip() -> None:
    venv_check = subprocess.run(
        [sys.executable, "-m", "venv", "--help"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    ensurepip_check = subprocess.run(
        [sys.executable, "-m", "ensurepip", "--version"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if venv_check.returncode != 0 or ensurepip_check.returncode != 0:
        pytest.skip("python venv with pip is required for shell pip install load tests")


def _require_npm() -> str:
    npm_path = shutil.which("npm")
    if npm_path is None:
        pytest.skip("npm is required for shell npm install load tests")
    return npm_path


def _seed_many(env: ApiLoadEnv, files: Mapping[str, str | bytes]) -> None:
    changes: list[LayerChange] = []
    hasher = ContentHasher()
    for path, content in files.items():
        payload = content if isinstance(content, bytes) else content.encode("utf-8")
        source = env.source_root / f"{uuid.uuid4().hex}.bin"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(payload)
        changes.append(
            LayerChange(
                path=path,
                kind="write",
                content_hash=hasher.hash_bytes(payload),
                source_path=str(source),
            )
        )
    env.manager.publish_changes(changes)


def _seed_pip_install_fixture(
    env: ApiLoadEnv,
    *,
    package_count: int = INSTALL_PACKAGE_COUNT,
    files_per_package: int = INSTALL_FILES_PER_PACKAGE,
) -> tuple[str, ...]:
    files: dict[str, bytes] = {}
    wheels: list[str] = []
    for package_index in range(package_count):
        package_name = f"eos-load-pip-pkg-{package_index}"
        import_name = f"eos_load_pip_pkg_{package_index}"
        version = f"0.0.{package_index}"
        wheel_path = (
            f"fixtures/pip/{import_name}-{version}-py3-none-any.whl"
        )
        wheels.append(wheel_path)
        files[wheel_path] = _pip_package_wheel(
            distribution_name=package_name,
            import_name=import_name,
            version=version,
            package_index=package_index,
            files_per_package=files_per_package,
        )
    _seed_many(env, files)
    return tuple(wheels)


def _pip_package_wheel(
    *,
    distribution_name: str,
    import_name: str,
    version: str,
    package_index: int,
    files_per_package: int,
) -> bytes:
    normalized_name = distribution_name.replace("-", "_")
    dist_info = f"{normalized_name}-{version}.dist-info"
    record_paths: list[str] = []
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, mode="w", compression=zipfile.ZIP_DEFLATED) as wheel:
        record_paths.append(f"{import_name}/__init__.py")
        _add_zip_text(wheel, record_paths[-1], f"PACKAGE_INDEX = {package_index}\n")
        for file_index in range(files_per_package):
            record_paths.append(f"{import_name}/module_{file_index}.py")
            _add_zip_text(
                wheel,
                record_paths[-1],
                f"VALUE_{file_index} = 'pip-{package_index}-{file_index}'\n",
            )
            record_paths.append(f"{import_name}/data/data_{file_index}.txt")
            _add_zip_text(
                wheel,
                record_paths[-1],
                f"pip package {package_index} data {file_index}\n" + ("x" * 256) + "\n",
            )
        record_paths.append(f"{dist_info}/METADATA")
        _add_zip_text(
            wheel,
            record_paths[-1],
            (
                "Metadata-Version: 2.1\n"
                f"Name: {distribution_name}\n"
                f"Version: {version}\n"
            ),
        )
        record_paths.append(f"{dist_info}/WHEEL")
        _add_zip_text(
            wheel,
            record_paths[-1],
            (
                "Wheel-Version: 1.0\n"
                "Generator: EphemeralOS sandbox load test\n"
                "Root-Is-Purelib: true\n"
                "Tag: py3-none-any\n"
            ),
        )
        record_path = f"{dist_info}/RECORD"
        record_rows = [f"{path},," for path in record_paths]
        record_rows.append(f"{record_path},,")
        _add_zip_text(wheel, record_path, "\n".join(record_rows) + "\n")
    return stream.getvalue()


def _add_zip_text(wheel: zipfile.ZipFile, name: str, content: str) -> None:
    info = zipfile.ZipInfo(name)
    info.date_time = (1980, 1, 1, 0, 0, 0)
    info.external_attr = 0o644 << 16
    wheel.writestr(info, content.encode("utf-8"))


def _seed_npm_install_fixture(
    env: ApiLoadEnv,
    *,
    package_count: int = INSTALL_PACKAGE_COUNT,
    files_per_package: int = INSTALL_FILES_PER_PACKAGE,
) -> tuple[str, ...]:
    files: dict[str, bytes] = {}
    tarballs: list[str] = []
    for package_index in range(package_count):
        package_name = f"eos-load-npm-pkg-{package_index}"
        tarball_path = f"fixtures/npm/{package_name}.tgz"
        tarballs.append(tarball_path)
        files[tarball_path] = _npm_package_tarball(
            package_name=package_name,
            package_index=package_index,
            files_per_package=files_per_package,
        )
    _seed_many(env, files)
    return tuple(tarballs)


def _npm_package_tarball(
    *,
    package_name: str,
    package_index: int,
    files_per_package: int,
) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        _add_tar_text(
            archive,
            "package/package.json",
            json.dumps(
                {
                    "name": package_name,
                    "version": f"0.0.{package_index}",
                    "main": "index.js",
                    "files": ["index.js", "lib", "data"],
                },
                sort_keys=True,
            )
            + "\n",
        )
        _add_tar_text(
            archive,
            "package/index.js",
            f"module.exports = {{ packageIndex: {package_index} }};\n",
        )
        for file_index in range(files_per_package):
            _add_tar_text(
                archive,
                f"package/lib/file_{file_index}.js",
                (
                    f"exports.value = 'npm-{package_index}-{file_index}';\n"
                    f"exports.index = {file_index};\n"
                ),
            )
            _add_tar_text(
                archive,
                f"package/data/data_{file_index}.txt",
                f"npm package {package_index} data {file_index}\n" + ("y" * 256) + "\n",
            )
    return stream.getvalue()


def _add_tar_text(archive: tarfile.TarFile, name: str, content: str) -> None:
    payload = content.encode("utf-8")
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    info.mode = 0o644
    info.mtime = 0
    archive.addfile(info, io.BytesIO(payload))


def _pip_install_command(package_dirs: Sequence[str], *, target: str) -> str:
    packages = " ".join(shlex.quote(path) for path in package_dirs)
    quoted_target = shlex.quote(target)
    venv = str(Path(target).parent / "venv")
    quoted_venv = shlex.quote(venv)
    venv_python = shlex.quote(f"{venv}/bin/python")
    return (
        f"mkdir -p {quoted_target}; "
        f"{shlex.quote(sys.executable)} -m venv {quoted_venv}; "
        f"PYTHONDONTWRITEBYTECODE=1 {venv_python} -m pip install "
        "--disable-pip-version-check --no-index --no-compile "
        f"--target {quoted_target} {packages}; "
        f"test -f {quoted_target}/eos_load_pip_pkg_0/__init__.py"
    )


def _npm_install_command(
    tarballs: Sequence[str],
    *,
    npm_path: str,
    project: str,
    cache: str,
) -> str:
    packages = " ".join(shlex.quote(path) for path in tarballs)
    quoted_project = shlex.quote(project)
    quoted_cache = shlex.quote(cache)
    return (
        f"mkdir -p {quoted_project} {quoted_cache}; "
        f"{shlex.quote(npm_path)} install "
        "--ignore-scripts --no-audit --no-fund "
        f"--cache {quoted_cache} --prefix {quoted_project} {packages}; "
        f"test -f {quoted_project}/node_modules/eos-load-npm-pkg-0/index.js"
    )
