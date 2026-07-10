#!/usr/bin/env bash
# FROZEN HISTORICAL ARTIFACT (operation-layout exempt, 2026-07-11).
# Preserves pre-migration E2E paths and is intentionally non-executable.
# Run the LOAD-COMBO-HTTP squash benchmark with span harvesting.
# Usage: run_combo.sh <label> <sessions> [extra pytest args...]
#   label    : names the report dir + log (e.g. baseline-s50, proto-s200)
#   sessions : SQUASH_COMBO_SESSIONS
set -uo pipefail

REPO_ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
cd "$REPO_ROOT"
export PATH="$PWD/bin:$PATH"

LABEL="${1:?label required}"
SESSIONS="${2:?sessions required}"
shift 2 || true

EXPT_DIR="docs/obsidian/ephemeral-os/implementation_plan/squash/experiments/performance-parallelization/perf-20260703-052525"
LOG="$EXPT_DIR/logs/pytest-$LABEL.log"
RUN_ID="perf-20260703-052525-$LABEL"

export SQUASH_HARVEST_OBS=1
export SQUASH_COMBO_SESSIONS="$SESSIONS"
export SQUASH_RUN_ID="$RUN_ID"

REPORT_DIR="cli-operation-e2e-live-test/manager/management/squash/test-reports/$RUN_ID/LOAD-COMBO-HTTP"

{
  echo "[run_combo] label=$LABEL sessions=$SESSIONS run_id=$RUN_ID started=$(date -u +%FT%TZ)"
  echo "[run_combo] report_dir=$REPORT_DIR"
} | tee "$LOG"

/opt/homebrew/bin/pytest \
  "cli-operation-e2e-live-test/manager/management/squash/test_squash_hard.py::test_squash_hard_catalog[LOAD-COMBO-HTTP]" \
  -s -q "$@" 2>&1 | tee -a "$LOG"
STATUS=${PIPESTATUS[0]}

echo "[run_combo] pytest exit=$STATUS finished=$(date -u +%FT%TZ)" | tee -a "$LOG"
if [ -f "$REPORT_DIR/observability.ndjson" ]; then
  echo "[run_combo] harvested $(wc -l < "$REPORT_DIR/observability.ndjson") span lines" | tee -a "$LOG"
else
  echo "[run_combo] WARNING: no observability.ndjson harvested" | tee -a "$LOG"
fi
exit "$STATUS"
