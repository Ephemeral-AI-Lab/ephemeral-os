"""Unit tests for namespace overlay payload dispatch."""

from __future__ import annotations

import json
import unittest.mock
from pathlib import Path
from unittest.mock import MagicMock

from sandbox.ephemeral_workspace.shell_contract import CommandExecRequest
from sandbox.overlay.layout import LayerPathsLayout
from sandbox.overlay.namespace import run_in_namespace


def _make_request(tmp_path: Path) -> CommandExecRequest:
    return CommandExecRequest(
        invocation_id="req-test",
        workspace_ref=str(tmp_path / "stack"),
        workspace_root="/testbed",
        command=("echo", "hi"),
    )


def test_namespace_payload_contains_layer_paths(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    spec = LayerPathsLayout(
        workspace_root="/testbed",
        layer_paths=(
            str(tmp_path / "layers" / "L1"),
            str(tmp_path / "layers" / "L2"),
        ),
        layer_storage_root=str(tmp_path / "layers"),
        writes=str(tmp_path / "scratch" / "upper"),
        kernel_scratch=str(tmp_path / "scratch" / "work"),
        scratch_root=str(tmp_path / "scratch"),
    )
    fake_proc = MagicMock()
    fake_proc.pid = 999
    fake_proc.poll.return_value = 0

    with unittest.mock.patch("subprocess.Popen", return_value=fake_proc), \
        unittest.mock.patch(
            "sandbox.overlay.namespace.wait_for_process_with_cancel",
            return_value=0,
        ):
        run_in_namespace(
            spec=spec,
            request=_make_request(tmp_path),
            run_dir=run_dir,
            timings={},
        )

    payload = json.loads((run_dir / "namespace-request.json").read_text())
    assert payload["layer_paths"] == list(spec.layer_paths)
    assert "lowerdir" not in payload
