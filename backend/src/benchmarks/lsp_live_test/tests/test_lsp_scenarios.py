"""Live e2e tests for LSP plugin tool calls on real Daytona.

Each scenario in ``LSP_SCENARIOS`` runs as a parametrized test against the
session-scoped ``lsp_sandbox`` fixture. The first scenario triggers
Pyright install + spawn; subsequent manifest changes refresh the stable
Pyright root without restarting the language server.

Skipped cleanly when Daytona is unavailable so unit-test collections don't
fail.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from benchmarks.lsp_live_test.runner import run_lsp_scenario
from benchmarks.lsp_live_test.scenarios import LSP_SCENARIOS, LspScenario


def _require_daytona_healthy() -> None:
    # __file__: backend/src/benchmarks/lsp_live_test/tests/test_lsp_scenarios.py
    # parents[0..5] walks up to <EphemeralOS>.
    repo_root = Path(__file__).resolve().parents[5]
    tier0_path = (
        repo_root
        / "backend"
        / "tests"
        / "live_e2e_test"
        / "_tools"
        / "tier0_health.py"
    )
    spec = importlib.util.spec_from_file_location(
        "_lsp_tier0_health", tier0_path
    )
    if spec is None or spec.loader is None:
        pytest.skip(f"tier0_health module not loadable from {tier0_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    result = module.probe_tier0()
    if not result.passed:
        pytest.skip(
            f"Tier-0 health gate failed: api_health={result.api_health!r} "
            f"notes={result.notes!r}"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario",
    LSP_SCENARIOS,
    ids=[scenario.name for scenario in LSP_SCENARIOS],
)
async def test_lsp_scenario_runs_end_to_end(
    scenario: LspScenario,
    lsp_sandbox: dict[str, object],
    lsp_repo_root: str,
) -> None:
    _require_daytona_healthy()
    sandbox_id = str(lsp_sandbox["sandbox_id"])
    report = await run_lsp_scenario(
        scenario,
        sandbox_id=sandbox_id,
        repo_root=lsp_repo_root,
    )
    print(
        f"\n[{scenario.name}] passed={report.passed} "
        f"duration={report.duration_s:.2f}s "
        f"warmup={report.warmup_duration_s:.2f}s "
        f"tool_durations={report.tool_durations_s}"
    )
    assert report.passed, f"scenario {scenario.name} failed: {report.failure}"
