"""Unit tests for ``RpcCiBackend.ensure_initialized`` + ``query_symbols``.

These tests use a fake transport that mocks ``exec`` so we can exercise the
Phase 1 path (bundle upload + indexer run + chunked snapshot download +
cache search) without paying live-Daytona time. The fake transport supports
the chunked-base64 read protocol (``wc -c`` + ``tail | head | base64``).
"""

from __future__ import annotations

import base64 as _b64
import json
import pickle
from typing import Any

import pytest

from sandbox.code_intelligence.backend import RpcCiBackend
from sandbox.code_intelligence.core.types import SymbolInfo, SymbolKind


def _sym(name: str, line: int = 1) -> SymbolInfo:
    return SymbolInfo(
        name=name,
        kind=SymbolKind.FUNCTION,
        file_path="/ws/foo.py",
        line=line,
        signature=f"def {name}()",
    )


class _FakeTransport:
    """Minimum SandboxTransport surface for the Phase 1 build_index path."""

    name = "fake"

    def __init__(
        self,
        *,
        snapshot_path: str = "/home/u/.cache/eos-ci/abc/v1/index.snapshot",
        cache: dict[str, list[SymbolInfo]] | None = None,
        ci_payload: dict[str, Any] | None = None,
        marker_hash_first: bool = False,
    ) -> None:
        self.exec_calls: list[tuple[str, str]] = []
        self._snapshot_path = snapshot_path
        cache = (
            cache
            if cache is not None
            else {
                "/ws/foo.py": [_sym("Bag"), _sym("Bagel")],
                "/ws/bar.py": [_sym("Other")],
            }
        )
        self._snapshot_blob = pickle.dumps(cache, protocol=5)
        self._ci_payload = ci_payload or {
            "ok": True,
            "mode": "full_build",
            "file_count": len(cache),
            "symbol_count": sum(len(v) for v in cache.values()),
            "snapshot_path": snapshot_path,
            "elapsed_s": 0.001,
        }
        self._marker_hash_first = marker_hash_first
        self._upload_seen = False
        self._daemon_alive = False
        self._socket_ready = False

    async def exec(
        self,
        sandbox_id: str,
        command: str,
        *,
        cwd: str | None = None,
        timeout: int | None = None,
    ) -> Any:
        del cwd, timeout
        self.exec_calls.append((sandbox_id, command))
        if 'printf %s "$HOME"' in command:
            return type("R", (), {"exit_code": 0, "stdout": "/home/u"})()
        if "daemon.pid" in command and "kill -0" in command:
            return type(
                "R",
                (),
                {"exit_code": 0 if self._daemon_alive else 1, "stdout": ""},
            )()
        if ".bundle-hash" in command and "tar -xzf" not in command:
            # Marker check — drive the upload path.
            return type("R", (), {"exit_code": 1, "stdout": ""})()
        if "tar -xzf" in command:
            self._upload_seen = True
            return type("R", (), {"exit_code": 0, "stdout": ""})()
        if "setsid nohup python3 -m sandbox.code_intelligence.in_sandbox" in command:
            self._daemon_alive = True
            self._socket_ready = True
            return type("R", (), {"exit_code": 0, "stdout": "1234\n"})()
        if command.startswith("test -S") and "daemon.sock" in command:
            return type(
                "R",
                (),
                {"exit_code": 0 if self._socket_ready else 1, "stdout": ""},
            )()
        if "kill -TERM" in command and "daemon.pid" in command:
            self._daemon_alive = False
            self._socket_ready = False
            return type("R", (), {"exit_code": 0, "stdout": ""})()
        if "ci_index" in command:
            return type(
                "R",
                (),
                {"exit_code": 0, "stdout": json.dumps(self._ci_payload) + "\n"},
            )()
        if "echo $HOME" in command:
            return type("R", (), {"exit_code": 0, "stdout": "/home/u\n"})()
        # Chunked snapshot read protocol.
        if "wc -c" in command and self._snapshot_path in command:
            return type(
                "R",
                (),
                {"exit_code": 0, "stdout": f"{len(self._snapshot_blob)}\n"},
            )()
        if command.startswith("dd if=") and "base64" in command:
            # Canonical command: dd if=<path> bs=<chunk> count=1 skip=<idx> ...
            try:
                bs_str = command.split(" bs=", 1)[1].split(" ", 1)[0]
                chunk_size = int(bs_str)
                skip_str = command.split("skip=", 1)[1].split(" ", 1)[0]
                idx = int(skip_str)
            except (IndexError, ValueError):
                return type("R", (), {"exit_code": 1, "stdout": "parse error"})()
            start = idx * chunk_size
            end = min(start + chunk_size, len(self._snapshot_blob))
            piece = self._snapshot_blob[start:end]
            return type(
                "R",
                (),
                {"exit_code": 0, "stdout": _b64.b64encode(piece).decode("ascii")},
            )()
        return type("R", (), {"exit_code": 0, "stdout": ""})()

    async def read_bytes(self, sandbox_id: str, path: str) -> bytes:
        del sandbox_id, path  # noqa - unused; chunked exec path now used
        raise NotImplementedError(
            "RpcCiBackend uses chunked-base64 over exec, not read_bytes"
        )

    async def read_bytes_batch(
        self, sandbox_id: str, paths: list[str]
    ) -> dict[str, bytes | None]:  # pragma: no cover - unused on this path
        del sandbox_id, paths
        return {}

    async def write_bytes(
        self, sandbox_id: str, path: str, content: bytes
    ) -> None:  # pragma: no cover - unused on this path
        del sandbox_id, path, content

    async def apply_diff_batch_checked(
        self, sandbox_id: str, specs: list[Any]
    ) -> Any:  # pragma: no cover
        del sandbox_id, specs
        raise NotImplementedError

    async def search(
        self, sandbox_id: str, pattern: str, **_: Any
    ) -> list[Any]:  # pragma: no cover
        del sandbox_id, pattern
        return []

    async def list_paths(
        self, sandbox_id: str, glob: str, **_: Any
    ) -> list[str]:  # pragma: no cover
        del sandbox_id, glob
        return []


def test_ensure_initialized_uploads_runs_and_caches_snapshot(
    tmp_path: Any,
) -> None:
    transport = _FakeTransport()
    backend = RpcCiBackend(
        sandbox_id="sb-test",
        workspace_root="/ws",
        transport=transport,  # type: ignore[arg-type]
    )

    assert backend.is_initialized is False
    ok = backend.ensure_initialized(wait=True)
    assert ok is True
    assert backend.is_initialized is True
    assert backend._cached_file_count == 2
    assert backend._cached_symbol_count == 3
    assert backend._snapshot_bytes > 0
    # Daemon ensure + bundle upload + ci_index = several exec calls minimum.
    assert len(transport.exec_calls) >= 3
    assert any("setsid nohup" in cmd for _, cmd in transport.exec_calls)
    assert any("ci_index" in cmd for _, cmd in transport.exec_calls)


def test_ensure_initialized_idempotent_on_second_call() -> None:
    transport = _FakeTransport()
    backend = RpcCiBackend(
        sandbox_id="sb-test",
        workspace_root="/ws",
        transport=transport,  # type: ignore[arg-type]
    )
    backend.ensure_initialized(wait=True)
    n_after_first = len(transport.exec_calls)
    backend.ensure_initialized(wait=True)
    # Second call must short-circuit on the lock check (no new exec calls).
    assert len(transport.exec_calls) == n_after_first


def test_query_symbols_finds_substring_match() -> None:
    transport = _FakeTransport()
    backend = RpcCiBackend(
        sandbox_id="sb-test",
        workspace_root="/ws",
        transport=transport,  # type: ignore[arg-type]
    )
    backend.ensure_initialized(wait=True)

    results = backend.query_symbols("bag")
    names = sorted(s.name for s in results)
    assert names == ["Bag", "Bagel"]


def test_query_symbols_empty_query_returns_empty() -> None:
    transport = _FakeTransport()
    backend = RpcCiBackend(
        sandbox_id="sb-test",
        workspace_root="/ws",
        transport=transport,  # type: ignore[arg-type]
    )
    backend.ensure_initialized(wait=True)

    assert backend.query_symbols("") == []
    assert backend.query_symbols("   ") == []


def test_query_symbols_returns_empty_before_initialization() -> None:
    transport = _FakeTransport()
    backend = RpcCiBackend(
        sandbox_id="sb-test",
        workspace_root="/ws",
        transport=transport,  # type: ignore[arg-type]
    )
    # No ensure_initialized call.
    assert backend.query_symbols("Bag") == []


def test_ensure_initialized_raises_on_indexer_failure() -> None:
    transport = _FakeTransport(
        ci_payload={"ok": False, "error": "boom", "elapsed_s": 0.0},
    )
    backend = RpcCiBackend(
        sandbox_id="sb-broken",
        workspace_root="/ws",
        transport=transport,  # type: ignore[arg-type]
    )
    with pytest.raises(RuntimeError, match="ci_index reported failure"):
        backend.ensure_initialized(wait=True)
    assert backend.is_initialized is False


def test_other_methods_still_raise_not_implemented() -> None:
    transport = _FakeTransport()
    backend = RpcCiBackend(
        sandbox_id="sb-test",
        workspace_root="/ws",
        transport=transport,  # type: ignore[arg-type]
    )
    with pytest.raises(NotImplementedError):
        backend.warmup()
    with pytest.raises(NotImplementedError):
        backend.list_folder_files("/ws")
    backend.dispose()
