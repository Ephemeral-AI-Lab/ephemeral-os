"""Unit tests for code intelligence file discovery helpers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from sandbox.code_intelligence.indexing.file_discovery import (
    collect_local_files,
    read_file_content,
)


def test_collect_local_files_filters_supported_python_files(tmp_path: Path) -> None:
    keep = tmp_path / "pkg" / "app.py"
    skip_ext = tmp_path / "pkg" / "build.bin"
    skip_dir = tmp_path / "node_modules" / "skip.py"
    keep.parent.mkdir()
    skip_dir.parent.mkdir()
    keep.write_text("VALUE = 1\n", encoding="utf-8")
    skip_ext.write_text("binary-ish\n", encoding="utf-8")
    skip_dir.write_text("SKIP = True\n", encoding="utf-8")

    assert collect_local_files(tmp_path, max_files=10) == [keep]


def test_read_file_content_ignores_process_exec_and_uses_fs_download(tmp_path: Path) -> None:
    target = str(tmp_path / "remote.py")

    class _Process:
        def exec(self, *args, **kwargs):
            del args, kwargs
            raise AssertionError("process.exec should not be used")

    class _Fs:
        async def download_file(self, path: str) -> bytes:
            assert path == target
            return b"VALUE = 1\n"

    sandbox = SimpleNamespace(process=_Process(), fs=_Fs())

    assert read_file_content(target, sandbox=sandbox) == "VALUE = 1\n"
