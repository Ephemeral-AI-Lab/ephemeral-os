"""Tests for local/sandbox-aware content reads."""

from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

from code_intelligence.routing.content_manager import ContentManager


class _RecordingProcess:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def exec(self, command: str):
        self.commands.append(command)
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return SimpleNamespace(
            result=(proc.stdout or "") + (proc.stderr or ""),
            exit_code=proc.returncode,
        )


class _FakeDaytonaFs:
    __module__ = "daytona_sdk._sync.filesystem"

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.requests: list[str] = []

    def download_files(self, requests):
        self.requests.extend(request.source for request in requests)
        return [
            SimpleNamespace(source=request.source, result=self.files.get(request.source))
            for request in requests
        ]


def test_read_many_reads_local_files(tmp_path: Path) -> None:
    a = tmp_path / "a.py"
    missing = tmp_path / "missing.py"
    a.write_text("A = 1\n", encoding="utf-8")

    content = ContentManager(str(tmp_path))

    result = content.read_many([str(a), str(missing)], allow_missing=True)

    assert result[str(a)] == ("A = 1\n", True)
    assert result[str(missing)] == ("", False)


def test_read_many_prefers_real_daytona_batch_download(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class FileDownloadRequest:
        def __init__(self, source: str) -> None:
            self.source = source

    common = types.ModuleType("daytona_sdk.common")
    filesystem = types.ModuleType("daytona_sdk.common.filesystem")
    filesystem.FileDownloadRequest = FileDownloadRequest
    monkeypatch.setitem(sys.modules, "daytona_sdk.common", common)
    monkeypatch.setitem(sys.modules, "daytona_sdk.common.filesystem", filesystem)

    a = str(tmp_path / "a.py")
    b = str(tmp_path / "b.py")
    process = _RecordingProcess()
    fs = _FakeDaytonaFs({a: b"A = 1\n", b: b"B = 2\n"})
    sandbox = SimpleNamespace(fs=fs, process=process)
    content = ContentManager(str(tmp_path), sandbox=sandbox)

    result = content.read_many([a, b, a], allow_missing=False)

    assert result[a] == ("A = 1\n", True)
    assert result[b] == ("B = 2\n", True)
    assert fs.requests == [a, b]
    assert process.commands == []


def test_read_many_batches_remote_exec(tmp_path: Path) -> None:
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text("A = 1\n", encoding="utf-8")
    b.write_text("B = 2\n", encoding="utf-8")

    process = _RecordingProcess()
    sandbox = SimpleNamespace(process=process)
    content = ContentManager(str(tmp_path), sandbox=sandbox)

    result = content.read_many([str(a), str(b), str(a)], allow_missing=False)

    assert result[str(a)] == ("A = 1\n", True)
    assert result[str(b)] == ("B = 2\n", True)
    assert len(process.commands) == 1


def test_read_many_allows_missing_remote_files(tmp_path: Path) -> None:
    a = tmp_path / "a.py"
    missing = tmp_path / "missing.py"
    a.write_text("A = 1\n", encoding="utf-8")

    sandbox = SimpleNamespace(process=_RecordingProcess())
    content = ContentManager(str(tmp_path), sandbox=sandbox)

    result = content.read_many([str(a), str(missing)], allow_missing=True)

    assert result[str(a)] == ("A = 1\n", True)
    assert result[str(missing)] == ("", False)

