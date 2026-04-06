#!/usr/bin/env bash
# Run e2e live tests.
# Usage: ./scripts/run_e2e_tests.sh <name>    # run a specific test file
#        ./scripts/run_e2e_tests.sh all        # run all live e2e tests
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

# Live tests (hit real APIs via EvalAgent/DB registry)
LIVE_TESTS=(
    test_anthropic_live.py              # Anthropic client streaming protocol
    test_tool_selection_eval.py         # LLM tool selection accuracy
    test_background_live.py             # Background task execution
    test_background_reminder_live.py    # Ephemeral background reminders
    test_background_context_live.py     # Context pressure with background tasks
    test_background_autonomy_live.py    # LLM autonomous background decisions
    test_live_api.py                    # Live API integration
    test_live_full_run.py               # Complete agent run with metrics
    test_live_sandbox_agents.py         # Sandbox tool calling
    test_live_agent_react_landing.py    # React page agent
    test_live_nextjs_sandbox.py         # Next.js sandbox agent
    test_live_minimax_comprehensive.py  # MiniMax comprehensive tests
)

# Mock/unit tests (no real API needed)
MOCK_TESTS=(
    test_chat_flow.py                   # Chat SSE event flow
    test_agent_toolkits_skills.py       # Toolkit/skill registration
    test_compaction.py                  # Context compaction
    test_code_intelligence.py           # Code intelligence service
    test_daytona_toolkit_comprehensive.py # Daytona toolkit unit tests
    test_multi_tool_e2e.py              # Multi-tool execution
    test_tool_cancel_e2e.py             # Tool cancellation
    test_minimax_agent.py               # MiniMax agent (server-based)
    test_anthropic_native_agent.py      # Anthropic native agent (server-based)
    test_agentic_loop_e2e.py            # Agentic loop (server-based)
)

_run_batch() {
    local label="$1"
    shift
    local tests=("$@")
    local passed=0 failed=0 skipped=0

    for test_file in "${tests[@]}"; do
        echo ""
        echo "================================================================"
        echo "  Running: $test_file"
        echo "================================================================"

        if $PYTEST "$E2E_DIR/$test_file" -v --tb=short --log-cli-level=WARNING; then
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
    echo "  all       Run all live e2e tests"
    echo "  mock      Run all mock/unit e2e tests"
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
    exit 0
fi

NAME="$1"

case "$NAME" in
    list)
        echo "Live tests (require API credentials):"
        for t in "${LIVE_TESTS[@]}"; do echo "  ${t%.py}"; done
        echo ""
        echo "Mock tests (no API needed):"
        for t in "${MOCK_TESTS[@]}"; do echo "  ${t%.py}"; done
        exit 0
        ;;
    all)
        _run_batch "Live E2E" "${LIVE_TESTS[@]}"
        exit $?
        ;;
    mock)
        _run_batch "Mock E2E" "${MOCK_TESTS[@]}"
        exit $?
        ;;
esac

# Find matching test file across both lists
MATCH=""
for t in "${LIVE_TESTS[@]}" "${MOCK_TESTS[@]}"; do
    if [[ "$t" == *"$NAME"* ]]; then
        MATCH="$t"
        break
    fi
done

if [[ -z "$MATCH" ]]; then
    echo "No test matching '$NAME'. Run '$0 list' to see available tests."
    exit 1
fi

echo "Running: $MATCH"
$PYTEST "$E2E_DIR/$MATCH" -v -s --tb=short --log-cli-level=WARNING
