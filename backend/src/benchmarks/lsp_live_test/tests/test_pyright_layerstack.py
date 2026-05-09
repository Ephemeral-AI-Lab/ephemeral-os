"""Live Pyright + layer-stack write/edit scenario."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from benchmarks.lsp_live_test.pyright_layerstack import (
    PyrightLayerStackReport,
    run_pyright_layerstack_complex_scenario,
)

pytestmark = pytest.mark.live


def _require_daytona_healthy() -> None:
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
        "_lsp_pyright_tier0_health", tier0_path
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
async def test_pyright_layerstack_complex_write_edit_cycle(
    lsp_sandbox: dict[str, object],
    lsp_repo_root: str,
) -> None:
    _require_daytona_healthy()
    sandbox_id = str(lsp_sandbox["sandbox_id"])
    report = await run_pyright_layerstack_complex_scenario(
        sandbox_id=sandbox_id,
        repo_root=lsp_repo_root,
    )
    _print_report(report)

    assert report.passed, report.failure
    assert [stage.name for stage in report.stages] == [
        "initial_writes_clean",
        "edit_model_type_error",
        "edit_service_clean_again",
        "new_consumer_type_error",
        "edit_consumer_clean_final",
    ]
    assert len(report.mutations) == 9


def _print_report(report: PyrightLayerStackReport) -> None:
    print(
        f"\n[pyright-layerstack] passed={report.passed} "
        f"duration={report.duration_s:.2f}s "
        f"install={report.install_duration_s:.2f}s"
    )
    for mutation in report.mutations:
        print(
            f"  mutation {mutation.name} "
            f"wall={mutation.duration_s:.3f}s "
            f"timings={mutation.timings}"
        )
    for stage in report.stages:
        print(
            f"  stage {stage.name} "
            f"manifest={stage.manifest_version} "
            f"wall={stage.duration_s:.3f}s "
            f"snapshot={stage.timings} "
            f"lsp_wall={stage.lsp.get('probe_wall_s')}"
        )
