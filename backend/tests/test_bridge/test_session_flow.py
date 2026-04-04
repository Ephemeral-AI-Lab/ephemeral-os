"""More realistic bridge session flow tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from ephemeralos.bridge import build_sdk_url, decode_work_secret, encode_work_secret, spawn_session
from ephemeralos.bridge.types import WorkSecret


@pytest.mark.asyncio
async def test_bridge_session_writes_output_in_cwd(tmp_path: Path):
    handle = await spawn_session(
        session_id="bridge-flow",
        command="printf 'bridge flow ok' > bridge.txt",
        cwd=tmp_path,
    )
    await handle.process.wait()
    assert handle.process.returncode == 0
    assert (tmp_path / "bridge.txt").read_text(encoding="utf-8") == "bridge flow ok"


def test_bridge_secret_and_url_flow():
    secret = WorkSecret(version=1, session_ingress_token="bridge-token", api_base_url="http://localhost:8080")
    encoded = encode_work_secret(secret)
    decoded = decode_work_secret(encoded)
    url = build_sdk_url(decoded.api_base_url, "session-123")

    assert decoded == secret
    assert url == "ws://localhost:8080/v2/session_ingress/ws/session-123"
