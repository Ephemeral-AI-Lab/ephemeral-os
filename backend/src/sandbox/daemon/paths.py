"""Shared host/runtime daemon wire paths."""

from __future__ import annotations

BUNDLE_REMOTE_DIR = "/tmp/eos-sandbox-runtime"
"""Remote directory where the in-sandbox runtime bundle is extracted."""

BUNDLE_HASH_MARKER = f"{BUNDLE_REMOTE_DIR}/.bundle-hash"
BUNDLE_REMOTE_TARBALL = f"{BUNDLE_REMOTE_DIR}/bundle.tar.gz"

DAEMON_SOCKET_PATH = f"{BUNDLE_REMOTE_DIR}/runtime.sock"
DAEMON_PID_PATH = f"{BUNDLE_REMOTE_DIR}/runtime.pid"
DAEMON_LOG_PATH = f"{BUNDLE_REMOTE_DIR}/runtime.log"
DAEMON_ENV_SIGNATURE_PATH = f"{BUNDLE_REMOTE_DIR}/runtime.env"
DEFAULT_LAYER_STACK_ROOT = f"{BUNDLE_REMOTE_DIR}/layer-stack"

RUNTIME_SCRIPT_DIR = f"{BUNDLE_REMOTE_DIR}/sandbox/daemon/scripts"
DAEMON_THIN_CLIENT_PATH = f"{RUNTIME_SCRIPT_DIR}/thin_client.py"
DAEMON_LAUNCH_SCRIPT_PATH = f"{RUNTIME_SCRIPT_DIR}/launch_daemon.sh"

__all__ = [
    "BUNDLE_HASH_MARKER",
    "BUNDLE_REMOTE_DIR",
    "BUNDLE_REMOTE_TARBALL",
    "DAEMON_ENV_SIGNATURE_PATH",
    "DAEMON_LAUNCH_SCRIPT_PATH",
    "DAEMON_LOG_PATH",
    "DAEMON_PID_PATH",
    "DAEMON_SOCKET_PATH",
    "DAEMON_THIN_CLIENT_PATH",
    "DEFAULT_LAYER_STACK_ROOT",
    "RUNTIME_SCRIPT_DIR",
]
