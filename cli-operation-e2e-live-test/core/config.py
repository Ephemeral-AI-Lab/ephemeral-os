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

# Workspace-root variants. repo/ holds one subfolder per variant (e.g. testbed,
# special_case_b); each is a HOST directory the Docker backend bind-mounts into
# the sandbox as its workspace root (--workspace-root is a host path).
REPO_DIR = SUITE_DIR / "repo"
WORKSPACE_VARIANT = os.environ.get("E2E_WORKSPACE_VARIANT", "testbed")


def workspace_variant(name=None):
    """Absolute host path of a workspace variant under repo/."""
    return str(REPO_DIR / (name or WORKSPACE_VARIANT))


# Default workspace root = the selected variant. Override with E2E_WORKSPACE_ROOT
# to point at any absolute host directory directly.
WORKSPACE_ROOT = os.environ.get("E2E_WORKSPACE_ROOT", workspace_variant())

# Default workspace-session network profile (shared | isolated).
NETWORK_PROFILE = os.environ.get("E2E_NETWORK_PROFILE", "shared")

# Daemon/sandbox config YAML used by the gateway start script.
CONFIG_YAML = os.environ.get(
    "SANDBOX_GATEWAY_CONFIG_YAML", str(REPO_ROOT / "config" / "prd.yml")
)

# "1" -> cold-start the gateway with --rebuild-binary (the documented path).
REBUILD_BINARY = os.environ.get("E2E_REBUILD_BINARY", "1")
