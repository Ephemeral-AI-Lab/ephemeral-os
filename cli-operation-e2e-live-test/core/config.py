"""Suite customization knobs and resolved paths.

Every value is overridable from the environment, e.g.::

    E2E_IMAGE=debian:12 E2E_WORKSPACE_ROOT=/work pytest manager
"""

import os
from pathlib import Path

SUITE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = SUITE_DIR.parent
BIN_DIR = REPO_ROOT / "bin"

SANDBOX_CLI = BIN_DIR / "sandbox-cli"
START_GATEWAY = BIN_DIR / "start-sandbox-docker-gateway"

# Docker image used for every sandbox (manager create_sandbox --image).
IMAGE = os.environ.get("E2E_IMAGE", "ubuntu:24.04")

# Absolute workspace root mounted inside each sandbox (container-internal path).
WORKSPACE_ROOT = os.environ.get("E2E_WORKSPACE_ROOT", "/testbed")

# Default workspace-session network profile (shared | isolated).
NETWORK_PROFILE = os.environ.get("E2E_NETWORK_PROFILE", "shared")

# Daemon/sandbox config YAML used by the gateway start script.
CONFIG_YAML = os.environ.get(
    "SANDBOX_GATEWAY_CONFIG_YAML", str(REPO_ROOT / "config" / "prd.yml")
)

# "1" -> cold-start the gateway with --rebuild-binary (the documented path).
REBUILD_BINARY = os.environ.get("E2E_REBUILD_BINARY", "1")
