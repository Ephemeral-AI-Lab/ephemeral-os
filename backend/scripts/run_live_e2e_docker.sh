#!/usr/bin/env bash
# Run the live_e2e sandbox suite under the Docker provider.
#
# REQUIRES: Linux host with Docker daemon running, EOS_LIVE_E2E_IMAGE set
# to a locally-available image tag with git, /testbed writable, and the
# runtime bundle marker baked in. See backend/tests/live_e2e_test/sandbox/README.md.
#
# Bails out cleanly on non-Linux (darwin / WSL without docker, etc.).

set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
    echo "run_live_e2e_docker.sh: Docker provider live-e2e requires Linux; saw $(uname -s)." >&2
    exit 2
fi

if [[ -z "${EOS_LIVE_E2E_IMAGE:-}" ]]; then
    echo "run_live_e2e_docker.sh: EOS_LIVE_E2E_IMAGE must be set to a docker image tag." >&2
    exit 2
fi

export EOS_SANDBOX_PROVIDER=docker

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}/.."

exec python -m backend.tests.live_e2e_test._tools.run_tiered \
    --provider docker \
    --tier 0,1,2,3,4,5,6 \
    "$@"
