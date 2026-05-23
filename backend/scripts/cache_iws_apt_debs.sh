#!/usr/bin/env bash
# cache_iws_apt_debs.sh — download the iproute2+nftables dep closure as .debs
# that the iws_sandbox test fixture can install offline.
#
# The session-scoped iws_sandbox fixture (see
#   backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/conftest.py)
# previously ran `apt-get update && apt-get install -y iproute2 nftables` on every
# fresh sweevo container. Ubuntu's apt mirror returns 502s through Docker
# Desktop's NAT often enough (~30% of runs in May 2026) that Tier 3 (network)
# and Tier 6 (concurrency) — which both genuinely need `ip` and `nft` — are
# un-runnable on a flaky network day.
#
# Resolution per NEXT-FIXES.md §1: ship the .deb closure with the repo (or in a
# committed cache directory) so the fixture can install offline.
#
# Usage:
#   bash backend/scripts/cache_iws_apt_debs.sh
#       Writes to backend/tests/_assets/iws_apt_cache/jammy-amd64/
#   bash backend/scripts/cache_iws_apt_debs.sh /some/other/dir
#
# Idempotent: re-running just re-downloads the .debs. Run again when the
# sweevo base ubuntu version bumps (jammy → noble would need a fresh cache).

set -euo pipefail

CACHE_DIR="${1:-backend/tests/_assets/iws_apt_cache/jammy-amd64}"

# Resolve to absolute path so the docker run -v mount works regardless of
# where the script was invoked.
mkdir -p "$CACHE_DIR"
CACHE_DIR="$(cd "$CACHE_DIR" && pwd)"

echo "Caching iproute2+nftables .deb closure into: $CACHE_DIR"

# Retry the docker run on 502s. The cache build itself goes through the
# same Ubuntu apt mirror this script is supposed to escape; a one-shot
# build needs to survive a transient 502 from archive.ubuntu.com.
attempts=0
max_attempts=3
sleep_base=10
while true; do
    attempts=$((attempts + 1))
    set +e
    docker run --rm --platform=linux/amd64 \
        -v "$CACHE_DIR:/cache" \
        -w /cache \
        ubuntu:22.04 \
        bash -c '
            set -e
            export DEBIAN_FRONTEND=noninteractive
            apt-get update -qq
            # --download-only puts .debs in /var/cache/apt/archives/. --reinstall
            # forces a download even for packages the base image already has, so
            # the closure is complete (the sweevo image lacks both binaries; the
            # ubuntu:22.04 base for the cache build has libc6, libcap2, … already
            # installed and would otherwise skip them).
            apt-get install -y -qq --download-only --reinstall \
                iproute2 nftables iputils-ping bind9-host \
                libmnl0 libnftnl11 libxtables12 libcap2 libbpf0 \
                libbsd0 libmd0 libnftables1 libjansson4 libelf1 \
                libcap2-bin
            cp /var/cache/apt/archives/*.deb /cache/
            ls /cache | wc -l
        '
    rc=$?
    set -e
    if [ "$rc" -eq 0 ]; then
        break
    fi
    if [ "$attempts" -ge "$max_attempts" ]; then
        echo "cache_iws_apt_debs.sh: docker run failed after $attempts attempts" >&2
        exit 1
    fi
    sleep_for=$((sleep_base * attempts))
    echo "cache_iws_apt_debs.sh: attempt $attempts failed (rc=$rc); retry in ${sleep_for}s" >&2
    sleep "$sleep_for"
done

echo "Cached .debs:"
ls -la "$CACHE_DIR"/*.deb | head -40
echo "Total: $(ls "$CACHE_DIR"/*.deb | wc -l) files, $(du -sh "$CACHE_DIR" | cut -f1)"
