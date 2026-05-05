#!/usr/bin/env bash
# Run curated e2e tests.
# Usage: ./scripts/run_e2e_tests.sh <name>    # run a specific test file
#        ./scripts/run_e2e_tests.sh all        # run curated live smoke tests
#
# Examples:
#   ./scripts/run_e2e_tests.sh anthropic_live
#   ./scripts/run_e2e_tests.sh background_live
#   ./scripts/run_e2e_tests.sh tool_selection_eval
#   ./scripts/run_e2e_tests.sh all

set -euo pipefail
cd "$(dirname "$0")/.."

PYTEST=".venv/bin/python -m pytest"
E2E_DIR="backend/tests/test_e2e"
COMMON_OPTS=(-o addopts= -v -s --tb=short)
LIVE_OPTS=(-m live --log-cli-level=INFO)
MOCK_OPTS=(-m "e2e and not live" --log-cli-level=INFO)

# Live smoke tests (hit real APIs / Daytona, but avoid high-cost model evals)
LIVE_TESTS=(
    test_anthropic_live.py              # Anthropic client streaming protocol
    test_bg_physical_cancel_live.py     # Physical process kill on cancel
    test_live_api.py                    # Live API integration
    test_live_daytona_opaque_dir_overlay.py # Overlay opaque-dir regression
    test_live_daytona_tool_occ_calls.py # Direct daytona_write_file/edit_file/codeact OCC tests
    test_tool_cancel_e2e.py             # Tool cancellation
)

# Model-behavior evals are useful before prompt/tool-contract changes, but too
# expensive and stochastic for the default live smoke batch.
EVAL_TESTS=(
    test_tool_selection_eval.py         # LLM tool selection accuracy
    test_agentic_loop_e2e.py            # Agentic loop tool behavior
)

# Long-running workflow checks are opt-in.
STRESS_TESTS=(
    test_bg_supernova_live.py           # Debug-fix-retest cycles
)

# Mock/unit tests (no real API needed)
MOCK_TESTS=(
    test_chat_flow.py                   # Chat SSE event flow
    test_agent_toolkits_skills.py       # Toolkit/skill registration
    test_daytona_toolkit_comprehensive.py # Daytona toolkit unit tests
)

_run_batch() {
    local label="$1"
    local mode="$2"
    shift 2
    local tests=("$@")
    local passed=0 failed=0 skipped=0
    local mode_opts=("${MOCK_OPTS[@]}")

    if [[ "$mode" == "live" ]]; then
        mode_opts=("${LIVE_OPTS[@]}")
    fi

    for test_file in "${tests[@]}"; do
        local target="$E2E_DIR/$test_file"
        if [[ ! -e "$target" ]]; then
            echo "Missing test target: $target" >&2
            return 1
        fi

        echo ""
        echo "================================================================"
        echo "  Running: $test_file"
        echo "================================================================"

        if $PYTEST "$target" "${COMMON_OPTS[@]}" "${mode_opts[@]}"; then
            ((passed++))
        else
            exit_code=$?
            if [[ $exit_code -eq 5 ]]; then
                echo "  -> SKIPPED (no credentials)"
                ((skipped++))
            else
                echo "  -> FAILED"
                ((failed++))
            fi
        fi
    done

    echo ""
    echo "================================================================"
    echo "  $label Summary"
    echo "================================================================"
    echo "  Passed:  $passed"
    echo "  Failed:  $failed"
    echo "  Skipped: $skipped"
    echo "================================================================"

    [[ $failed -gt 0 ]] && return 1
    return 0
}

if [[ $# -eq 0 ]]; then
    echo "Usage: $0 <test_name|command>"
    echo ""
    echo "Commands:"
    echo "  all       Run curated live smoke tests"
    echo "  mock      Run all mock/unit e2e tests"
    echo "  eval      Run model-behavior live evals"
    echo "  stress    Run long-running live workflows"
    echo "  list      List all available tests"
    echo ""
    echo "Live tests:"
    for t in "${LIVE_TESTS[@]}"; do
        echo "  ${t%.py}"
    done
    echo ""
    echo "Mock tests:"
    for t in "${MOCK_TESTS[@]}"; do
        echo "  ${t%.py}"
    done
    echo ""
    echo "Model eval tests:"
    for t in "${EVAL_TESTS[@]}"; do
        echo "  ${t%.py}"
    done
    echo ""
    echo "Stress tests:"
    for t in "${STRESS_TESTS[@]}"; do
        echo "  ${t%.py}"
    done
    exit 0
fi

NAME="$1"

case "$NAME" in
    list)
        echo "Live smoke tests (require API credentials):"
        for t in "${LIVE_TESTS[@]}"; do echo "  ${t%.py}"; done
        echo ""
        echo "Mock tests (no API needed):"
        for t in "${MOCK_TESTS[@]}"; do echo "  ${t%.py}"; done
        echo ""
        echo "Model eval tests (require API credentials):"
        for t in "${EVAL_TESTS[@]}"; do echo "  ${t%.py}"; done
        echo ""
        echo "Stress tests (require API credentials):"
        for t in "${STRESS_TESTS[@]}"; do echo "  ${t%.py}"; done
        exit 0
        ;;
    all)
        _run_batch "Live E2E" "live" "${LIVE_TESTS[@]}"
        exit $?
        ;;
    mock)
        _run_batch "Mock E2E" "mock" "${MOCK_TESTS[@]}"
        exit $?
        ;;
    eval)
        _run_batch "Model Eval E2E" "live" "${EVAL_TESTS[@]}"
        exit $?
        ;;
    stress)
        _run_batch "Stress E2E" "live" "${STRESS_TESTS[@]}"
        exit $?
        ;;
esac

# Find matching test file across both lists
MATCH=""
MATCH_MODE=""
for t in "${LIVE_TESTS[@]}" "${EVAL_TESTS[@]}" "${STRESS_TESTS[@]}"; do
    if [[ "$t" == *"$NAME"* ]]; then
        MATCH="$t"
        MATCH_MODE="live"
        break
    fi
done
if [[ -z "$MATCH" ]]; then
    for t in "${MOCK_TESTS[@]}"; do
        if [[ "$t" == *"$NAME"* ]]; then
            MATCH="$t"
            MATCH_MODE="mock"
            break
        fi
    done
fi

if [[ -z "$MATCH" ]]; then
    echo "No test matching '$NAME'. Run '$0 list' to see available tests."
    exit 1
fi

if [[ ! -e "$E2E_DIR/$MATCH" ]]; then
    echo "Missing test target: $E2E_DIR/$MATCH" >&2
    exit 1
fi

echo "Running: $MATCH"
if [[ "$MATCH_MODE" == "live" ]]; then
    $PYTEST "$E2E_DIR/$MATCH" "${COMMON_OPTS[@]}" "${LIVE_OPTS[@]}"
else
    $PYTEST "$E2E_DIR/$MATCH" "${COMMON_OPTS[@]}" "${MOCK_OPTS[@]}"
fi
