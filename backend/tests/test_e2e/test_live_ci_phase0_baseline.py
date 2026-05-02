"""Phase 0 baseline timing — live E2E against a real Daytona sandbox.

Establishes the canonical baseline timing JSON
(``_timings/phase_0_baseline_<ts>.json``) that every later phase's
``compare_to(...)`` references.

Phase 3.5 / 3.6: this module also captures the pre-rewire jedi.Script LSP
baseline (``_timings/phase_0_lsp_baseline_<ts>.json``) that Phase 3.6's
benchmark asserts against. The LSP baseline MUST be captured before the
Phase 3.6 rewire commit deletes ``python_backend.py`` — otherwise the
jedi numbers cannot be reproduced.

Run with:
    .venv/bin/pytest backend/tests/test_e2e/test_live_ci_phase0_baseline.py -m live -v -s
"""

from __future__ import annotations

import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

import pytest

from engine.testing.eval_agent import EvalAgent
from sandbox.api.bash import extract_exit_code, wrap_bash_command
from sandbox.client.async_ import get_async_sandbox
from sandbox.code_intelligence.core.types import EditSpec, WriteSpec
from sandbox.code_intelligence.mutations.patcher import SearchReplaceEdit
from sandbox.code_intelligence.service import CodeIntelligenceService

from ._timing_harness import TimingHarness

pytestmark = [pytest.mark.e2e, pytest.mark.live]

_DASK_SWEEVO_INSTANCE_ID = "dask__dask_2023.3.2_2023.4.0"
_DASK_SWEEVO_REPO_DIR = "/testbed"


def _flush_print(msg: str) -> None:
    """Print with immediate flush so progress shows under ``pytest -s``."""
    print(msg, flush=True)
    sys.stdout.flush()


@contextmanager
def _traced_step(harness: TimingHarness, name: str) -> Iterator[None]:
    """Wrap ``harness.step`` with mid-flight ``→`` / ``✓`` progress prints."""
    _flush_print(f"  → {name} ...")
    t0 = time.perf_counter()
    with harness.step(name):
        yield
    elapsed = time.perf_counter() - t0
    _flush_print(f"  ✓ {name} ({elapsed:.3f}s)")


@dataclass
class LivePhase0Env:
    sandbox_id: str
    raw_sandbox: Any
    home: str
    root_dir: str

    def exec(self, command: str, *, timeout: int = 60) -> tuple[int, str]:
        response = self.raw_sandbox.process.exec(
            wrap_bash_command(command),
            timeout=timeout,
        )
        output, exit_code = extract_exit_code(
            getattr(response, "result", "") or "",
            fallback_exit_code=getattr(response, "exit_code", None),
        )
        return exit_code, output

    def make_ci_service(self) -> CodeIntelligenceService:
        return CodeIntelligenceService(
            sandbox_id=self.sandbox_id,
            workspace_root=self.root_dir,
            sandbox=self.raw_sandbox,
        )


def _asyncio_run(coro: Any) -> Any:
    import asyncio

    return asyncio.run(coro)


@pytest.fixture(scope="module")
def live_phase0_env() -> LivePhase0Env:
    if not EvalAgent.has_daytona():
        pytest.skip("Daytona credentials not configured")

    from benchmarks.sweevo.dataset import select_sweevo_instance
    from benchmarks.sweevo.models import _CONDA_ACTIVATE
    from benchmarks.sweevo.sandbox import create_sweevo_test_sandbox
    from sandbox.testing import delete_test_sandbox, get_sandbox_service

    _flush_print(
        f"\n[fixture] provisioning sweevo sandbox {_DASK_SWEEVO_INSTANCE_ID} ..."
    )
    instance = select_sweevo_instance(instance_id=_DASK_SWEEVO_INSTANCE_ID)
    sandbox_name = f"ci-phase0-{uuid.uuid4().hex[:8]}"
    t0 = time.perf_counter()
    result = _asyncio_run(
        create_sweevo_test_sandbox(
            instance,
            sandbox_name=sandbox_name,
            repo_dir=_DASK_SWEEVO_REPO_DIR,
        )
    )
    sandbox_id = str(result["sandbox_id"])
    _flush_print(
        f"[fixture] sandbox {sandbox_id} provisioned in "
        f"{time.perf_counter() - t0:.1f}s"
    )
    try:
        raw_sandbox = get_sandbox_service().get_sandbox_object(sandbox_id)
        home_resp = raw_sandbox.process.exec("pwd", timeout=10)
        home = (getattr(home_resp, "result", "") or "").strip() or "/home/daytona"
        env = LivePhase0Env(
            sandbox_id=sandbox_id,
            raw_sandbox=raw_sandbox,
            home=home,
            root_dir=_DASK_SWEEVO_REPO_DIR,
        )
        # Smoke: confirm conda + repo are usable.
        _flush_print("[fixture] smoke-checking conda + python in sandbox ...")
        exit_code, output = env.exec(
            f"{_CONDA_ACTIVATE} && cd {_DASK_SWEEVO_REPO_DIR} && python --version",
            timeout=60,
        )
        assert exit_code == 0, output
        _flush_print(f"[fixture] sandbox ready: {output.strip()}")
        yield env
    finally:
        _flush_print(f"[fixture] tearing down sandbox {sandbox_id} ...")
        delete_test_sandbox(sandbox_id)
        _flush_print("[fixture] sandbox deleted")


@pytest.mark.asyncio
async def test_phase0_baseline_timings(live_phase0_env: LivePhase0Env) -> None:
    """Run every documented step + dump baseline JSON to ``_timings/``."""
    h = TimingHarness(phase=0, test_name="baseline_timings")
    env = live_phase0_env
    probe_path = f"{env.root_dir}/_phase0_probe.txt"

    _flush_print(f"\n[phase0] starting baseline run for {env.sandbox_id}")

    # Sandbox provisioning is owned by the module-scoped fixture; record a
    # zero-elapsed marker so the baseline shape is stable across runs.
    h.record("sandbox_create")
    h.record("sweevo_setup")

    with _traced_step(h, "ci_service_construct"):
        svc = env.make_ci_service()

    with _traced_step(h, "index_build_in_process"):
        svc.ensure_initialized(wait=True)
    h.record(
        "index_build_in_process",
        count=svc.symbol_index.indexed_files,
        bytes_=svc.symbol_index.size,
    )
    _flush_print(
        f"    [stats] indexed_files={svc.symbol_index.indexed_files} "
        f"symbol_index.size={svc.symbol_index.size}"
    )

    with _traced_step(h, "query_symbols_first"):
        first_results = svc.query_symbols("Bag")
    h.record("query_symbols_first", count=len(first_results))

    with _traced_step(h, "query_symbols_warm"):
        warm_results = svc.query_symbols("Bag")
    h.record("query_symbols_warm", count=len(warm_results))

    # Sync mutation hot-path runs first against the sweevo fixture's sync
    # ``raw_sandbox``. We defer ``svc.cmd`` (which requires an async
    # ``process.exec``) to last so the AsyncDaytona client's event-loop
    # state cannot conflict with the sync mutation pipeline.
    with _traced_step(h, "write_file_baseline"):
        write_result = svc.write_file(
            [WriteSpec(file_path=probe_path, content="hello\n", overwrite=True)],
        )
    assert write_result.success, f"write_file failed: {write_result.status}"

    with _traced_step(h, "edit_file_baseline"):
        edit_result = svc.edit_file(
            [
                EditSpec(
                    file_path=probe_path,
                    edits=[SearchReplaceEdit(old_text="hello\n", new_text="world\n")],
                )
            ]
        )
    assert edit_result.success, f"edit_file failed: {edit_result.status}"

    with _traced_step(h, "delete_file_baseline"):
        delete_result = svc.delete_file([probe_path])
    assert delete_result.success, f"delete_file failed: {delete_result.status}"

    # Resolve an async sandbox handle for ``svc.cmd``: AuditedCommandExecutor
    # requires ``process.exec`` to be async; the sweevo fixture's raw_sandbox
    # uses the sync Daytona SDK.
    _flush_print("  → resolving async sandbox handle for svc.cmd ...")
    async_sandbox = await get_async_sandbox(env.sandbox_id)
    _flush_print("  ✓ async sandbox handle ready")

    with _traced_step(h, "svc_cmd_baseline"):
        cmd_result = await svc.cmd(
            async_sandbox,
            "find /testbed -name '*.py' | wc -l",
        )
    h.record("svc_cmd_baseline", bytes_=len(str(getattr(cmd_result, "result", ""))))

    with _traced_step(h, "ci_service_dispose"):
        svc.dispose()

    # Sandbox dispose is owned by the fixture teardown; emit a marker for shape.
    h.record("sandbox_dispose")

    report = h.report()
    _flush_print("\n" + report)
    baseline_path = h.dump_json()
    _flush_print(f"\n[phase0] baseline saved at: {baseline_path}")

    # Soft assertions on shape — the baseline must contain every documented step.
    payload_names = [s["name"] for s in h.to_payload()["steps"]]
    expected = [
        "sandbox_create",
        "sweevo_setup",
        "ci_service_construct",
        "index_build_in_process",
        "query_symbols_first",
        "query_symbols_warm",
        "write_file_baseline",
        "edit_file_baseline",
        "delete_file_baseline",
        "svc_cmd_baseline",
        "ci_service_dispose",
        "sandbox_dispose",
    ]
    assert payload_names == expected, (
        f"baseline step order/coverage mismatch: {payload_names} != {expected}"
    )


def _find_python_symbol_position(env: LivePhase0Env, file_path: str) -> tuple[int, int]:
    """Locate the first ``def NAME(`` line in *file_path* (1-indexed line, 0-indexed col)."""
    code, output = env.exec(
        f"grep -nE '^(def |class )' {file_path} | head -1",
        timeout=20,
    )
    if code != 0 or not output.strip():
        return 1, 4
    first = output.splitlines()[0]
    line_str, _, _ = first.partition(":")
    try:
        return int(line_str), 4
    except ValueError:
        return 1, 4


def test_phase0_lsp_baseline_jedi(live_phase0_env: LivePhase0Env) -> None:
    """Capture jedi.Script LSP timings BEFORE Phase 3.6 deletes ``python_backend.py``.

    Phase 3.6's benchmark asserts the chosen backend (basedpyright / pyright)
    is at least 5x faster than this jedi baseline for ``find_definitions``
    and 10x faster for ``hover``. Once ``python_backend.py`` is removed by
    the rewire commit, this baseline cannot be reproduced.
    """
    h = TimingHarness(phase=0, test_name="lsp_baseline")
    env = live_phase0_env
    target_file = f"{env.root_dir}/dask/__init__.py"

    _flush_print(f"\n[phase0-lsp] starting LSP baseline run for {env.sandbox_id}")

    with _traced_step(h, "ci_service_construct"):
        svc = env.make_ci_service()
    with _traced_step(h, "index_build_in_process"):
        svc.ensure_initialized(wait=True)

    target_line, target_char = _find_python_symbol_position(env, target_file)
    _flush_print(
        f"  [phase0-lsp] target {target_file}:{target_line}:{target_char}"
    )

    # Cold first-query cost reported separately so it is not amortized into
    # warm-sample distributions.
    with _traced_step(h, "lsp_cold_find_definitions"):
        try:
            svc.find_definitions(target_file, "", target_line, target_char)
        except Exception as exc:
            _flush_print(f"  [phase0-lsp] cold find_definitions failed: {exc}")

    # Warm-up to allow any per-call jedi caching to settle.
    for _ in range(3):
        try:
            svc.find_definitions(target_file, "", target_line, target_char)
        except Exception:
            pass

    # 20-sample distributions for each LSP op. The op call may fall back to
    # the symbol-index path when LSP returns nothing — we record whatever
    # the user-facing call returns, since that is the metric Phase 3.6 must
    # beat (LSP-or-fallback).
    for step in h.step_repeat("find_definitions", n=20):
        with step:
            try:
                svc.find_definitions(target_file, "", target_line, target_char)
            except Exception:
                pass
    for step in h.step_repeat("find_references", n=20):
        with step:
            try:
                svc.find_references(target_file, "", target_line, target_char)
            except Exception:
                pass
    for step in h.step_repeat("hover", n=20):
        with step:
            try:
                svc.hover(target_file, target_line, target_char)
            except Exception:
                pass
    for step in h.step_repeat("diagnostics", n=20):
        with step:
            try:
                svc.diagnostics(target_file)
            except Exception:
                pass

    with _traced_step(h, "ci_service_dispose"):
        svc.dispose()

    report = h.report()
    _flush_print("\n" + report)
    baseline_path = h.dump_json()
    _flush_print(f"\n[phase0-lsp] baseline saved at: {baseline_path}")

    # Soft assertions — every LSP op must have a distribution.
    payload = h.to_payload()
    assert "find_definitions" in payload["distributions"]
    assert "find_references" in payload["distributions"]
    assert "hover" in payload["distributions"]
    assert "diagnostics" in payload["distributions"]
    for op in ["find_definitions", "find_references", "hover", "diagnostics"]:
        assert payload["distributions"][op]["n"] == 20, (
            f"{op}: expected 20 samples, got {payload['distributions'][op]['n']}"
        )
