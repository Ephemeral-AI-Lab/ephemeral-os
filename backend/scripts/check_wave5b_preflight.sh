#!/usr/bin/env bash
# Wave-5b enforcement hook per RFC §14.
#
# Rejects a commit if it touches daemon/service/ (the W5b inline target)
# without the immediately-preceding commit message containing the
# `wave-5b-preflight:` prefix. The pre-flight report (.planning/
# wave-5b-preflight.md) is BLOCKING and must land as its own commit
# before the W5b primary commit.
#
# This script is intended to be invoked as a pre-push gate or manually
# from CI. It exits 0 when no enforcement applies, 1 when the rule is
# violated, and prints the violation to stderr.

set -euo pipefail

HEAD_SHA="$(git rev-parse HEAD)"
HEAD_MSG="$(git log -1 --format=%B "${HEAD_SHA}")"
HEAD_FILES="$(git show --name-only --format= "${HEAD_SHA}")"

# Only enforce on commits that actually touch the W5b inline target zone.
# After W3 the path becomes daemon/service/; pre-W3 it is runtime/daemon/service/.
if ! echo "${HEAD_FILES}" | grep -qE '(^|/)(runtime|daemon)/service/'; then
    exit 0
fi

# Pre-flight commits identify themselves; skip enforcement on them.
if echo "${HEAD_MSG}" | head -n1 | grep -qE '^wave-5b-preflight:'; then
    exit 0
fi

# For W5b primary commits, require that HEAD-1 message starts with the
# pre-flight prefix.
PREV_MSG="$(git log -1 --format=%s HEAD~1 2>/dev/null || echo "")"
if ! echo "${PREV_MSG}" | grep -qE '^wave-5b-preflight:'; then
    echo "ERROR: HEAD touches daemon/service/ but HEAD-1 message does not begin with 'wave-5b-preflight:'." >&2
    echo "        Per RFC §14, the Wave-5b primary commit must follow a 'wave-5b-preflight:' commit." >&2
    echo "        HEAD-1 message was: ${PREV_MSG}" >&2
    exit 1
fi

# Also require that the W5b primary commit references the pre-flight sha.
PREV_SHA="$(git rev-parse HEAD~1)"
if ! echo "${HEAD_MSG}" | grep -qF "${PREV_SHA:0:7}"; then
    echo "WARNING: HEAD message does not reference the pre-flight commit SHA ${PREV_SHA:0:7}." >&2
    echo "         RFC §14 recommends 'Refs: <pre-flight-sha>' in the W5b primary commit body." >&2
fi

exit 0
