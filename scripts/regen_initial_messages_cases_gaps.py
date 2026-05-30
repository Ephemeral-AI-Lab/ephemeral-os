"""Generate cases 12/13/14/15 from existing scenario runs that close
the previously-flagged content gaps in the initial_messages_cases
directory.

* Case 12 — planner launch routed to close-only terminals in a child-workflow context
  (no submit_plan_defers_goal terminal). Source:
  pipeline.deferred_parent_planner_terminal_routing.

* Case 13 — planner that sees a *rich* `<attempt status="failed">` body
  (real plan_spec, real `<generator_outcomes>`, real
  `<evaluator_judgment>`). Source: pipeline.attempt_retry_evaluator_failure,
  iter1 attempt2 planner. Closes Gap 2.

* Case 14 — executor whose user_msg_1 carries a real `<dependency_results>`
  block (one upstream task). Source: pipeline.dependency_dag_serial,
  task `b` (deps: [a]). Closes Gap 3.

* Case 15 — evaluator that proceeds to `submit_evaluation_failure`. The
  evaluator user_msg_1 shape is identical to a passing evaluator's, but
  the case is captured for completeness on Gap 5. Source:
  pipeline.attempt_retry_evaluator_failure, iter1 attempt1 evaluator.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path("/Users/yifanxu/machine_learning/LoVC/EphemeralOS")
CASES_DIR = REPO / "docs" / "reports" / "initial_messages_cases"

# The pytest audit_dir fixture defaults to ``.sweevo_runs`` relative to
# cwd. When pytest runs from the repo root, runs land under
# ``<repo>/.sweevo_runs``; older runs may sit under
# ``<repo>/backend/.sweevo_runs``. Scan both.
_AUDIT_BASES = (
    REPO / ".sweevo_runs" / "scenario_logs",
    REPO / "backend" / ".sweevo_runs" / "scenario_logs",
)


def _text_of(row: dict) -> str:
    return "\n".join(
        b.get("text", "")
        for b in row.get("content", []) or []
        if isinstance(b, dict) and b.get("type") == "text"
    )


def _read_initial_rows(path: Path) -> tuple[str, str, str, str]:
    rows: list[dict] = []
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
            if len(rows) == 4:
                break
    return (
        _text_of(rows[0]) if rows else "",
        _text_of(rows[1]) if len(rows) > 1 else "",
        _text_of(rows[2]) if len(rows) > 2 else "",
        _text_of(rows[3]) if len(rows) > 3 else "",
    )


def _latest_run(scenario: str) -> Path:
    runs: list[Path] = []
    for base in _AUDIT_BASES:
        candidate = base / scenario
        if candidate.is_dir():
            runs.extend(p for p in candidate.iterdir() if p.is_dir())
    if not runs:
        raise SystemExit(
            f"no runs for scenario {scenario!r} under any of {list(_AUDIT_BASES)!r}"
        )
    return max(runs, key=lambda p: p.stat().st_mtime)


def _write_case(
    *,
    case_path: Path,
    title: str,
    source: str,
    notes: str,
    system: str,
    user_msg_1: str,
    user_msg_2: str = "",
    user_msg_3: str = "",
) -> None:
    parts = [f"# {title}", f"- source: `{source}`", f"- notes: {notes}", "", "## system", "", "```", system.rstrip(), "```", "", "## user_msg_1", "", "```", user_msg_1.rstrip(), "```"]
    if user_msg_2:
        parts.extend(["", "## user_msg_2", "", "```", user_msg_2.rstrip(), "```"])
    if user_msg_3:
        parts.extend([
            "", "## user_msg_3 — row 4 (skill + terminal_tool_selection)", "", "```", user_msg_3.rstrip(), "```",
        ])
    parts.append("")
    case_path.write_text("\n".join(parts))


def case_12_planner_child_workflow_close_only() -> None:
    """Capture case 12 from the deferred_parent_planner_terminal_routing scenario.

    The scenario submits a partial plan in the parent workflow, then delegates a
    child workflow. The child workflow's planner keeps the single ``planner`` profile,
    but launch-time terminal routing removes ``submit_plan_defers_goal``.
    """
    run = _latest_run("pipeline.deferred_parent_planner_terminal_routing")
    # The child workflow directory is ``workflow_02_*`` (root is workflow_01).
    candidates = list(run.rglob("workflow_02_*/iteration_01_*/attempt_01_*/01_planner_*:planner/message.jsonl"))
    assert len(candidates) == 1, candidates
    jsonl = candidates[0]
    rel = jsonl.relative_to(run)
    system, um1, um2, um3 = _read_initial_rows(jsonl)
    _write_case(
        case_path=CASES_DIR
        / "12_planner__child_workflow__delegated_from_deferring_parent.md",
        title=(
            "planner - child workflow delegated from a partial-plan parent "
            "(only `submit_plan_closes_goal` is available)"
        ),
        source=(
            "pipeline.deferred_parent_planner_terminal_routing/"
            f"{run.name}/{rel}"
        ),
        notes=(
            "The parent attempt submitted a partial plan that delegated work to a "
            "child workflow. The child workflow still launches the ``planner`` profile, "
            "but terminal routing uses ``nested_workflow_depth_gt_1`` to expose only "
            "``submit_plan_closes_goal``. Row 4's "
            "``<terminal_tool_selection>`` block therefore lists only that "
            "terminal."
        ),
        system=system,
        user_msg_1=um1,
        user_msg_2=um2,
        user_msg_3=um3,
    )
    print(
        f"wrote {CASES_DIR}/"
        "12_planner__child_workflow__delegated_from_deferring_parent.md"
    )


def case_13_planner_after_evaluator_failure() -> None:
    run = _latest_run("pipeline.attempt_retry_evaluator_failure")
    # iter1 attempt 2 planner
    candidates = list(run.rglob("iteration_01_*/attempt_02_*/01_planner_*:planner/message.jsonl"))
    assert len(candidates) == 1, candidates
    jsonl = candidates[0]
    rel = jsonl.relative_to(run)
    system, um1, um2, um3 = _read_initial_rows(jsonl)
    _write_case(
        case_path=CASES_DIR / "13_planner__iter1_attempt2__after_evaluator_failure__rich_failed_body.md",
        title="planner — iteration 1, attempt 2 (after evaluator failure; rich `<attempt status=\"prior\" verdict=\"fail\">` body with real plan_spec, real per-task summaries, real evaluator commentary)",
        source=f"pipeline.attempt_retry_evaluator_failure/{run.name}/{rel}",
        notes=(
            "Closes Gap 2 in the original gap report. The prior attempt's "
            "plan was valid, executor ran, evaluator returned "
            "`submit_evaluation_failure` — so the "
            "`<attempt status=\"prior\" verdict=\"fail\">` block carries "
            "real flat-child bodies: `<plan_spec>`, `<status_summary>`, "
            "per-task `<task>` summaries, `<evaluation_criteria>`, "
            "`<evaluator_summary>`, and `<failed_criteria>` — no wrapping "
            "`<attempt_plan>`/`<generator_outcomes>`/`<evaluator_judgment>` "
            "groups."
        ),
        system=system,
        user_msg_1=um1,
        user_msg_2=um2,
        user_msg_3=um3,
    )
    print(f"wrote {CASES_DIR}/13_planner__iter1_attempt2__after_evaluator_failure__rich_failed_body.md")


def case_14_executor_with_dependency_results() -> None:
    run = _latest_run("pipeline.dependency_dag_serial")
    candidates = list(run.rglob("03_executor_*:gen:b/message.jsonl"))
    assert len(candidates) == 1, candidates
    jsonl = candidates[0]
    rel = jsonl.relative_to(run)
    system, um1, um2, um3 = _read_initial_rows(jsonl)
    _write_case(
        case_path=CASES_DIR / "14_executor__dependency_results__has_deps_branch.md",
        title="executor — has_deps branch (generator task `b`, deps: [`a`]); user_msg_1 carries flat `<dependency>` siblings",
        source=f"pipeline.dependency_dag_serial/{run.name}/{rel}",
        notes=(
            "Closes Gap 3 in the original gap report. The scenario submits a serial "
            "DAG `a → b → c`; task `b` runs with `deps=[\"a\"]`, so its composer "
            "renders one flat `<dependency id=...>` block per upstream task "
            "between `<plan_spec>` and `<assigned_task>` (no wrapping group). "
            "Row 3's `<Task Guidance>` follows the registry-driven shape: a "
            "deterministic outline (`<plan_spec>`, `<dependency>`, `<assigned_task>`) "
            "plus the executor's role directive. "
            "This is the variant the existing initial_messages scenario could not "
            "exercise because its plans only have single-task DAGs."
        ),
        system=system,
        user_msg_1=um1,
        user_msg_2=um2,
        user_msg_3=um3,
    )
    print(f"wrote {CASES_DIR}/14_executor__dependency_results__has_deps_branch.md")


def case_15_evaluator_pre_failure_submission() -> None:
    run = _latest_run("pipeline.attempt_retry_evaluator_failure")
    candidates = list(run.rglob("iteration_01_*/attempt_01_*/03_evaluator_*:evaluator/message.jsonl"))
    assert len(candidates) == 1, candidates
    jsonl = candidates[0]
    rel = jsonl.relative_to(run)
    system, um1, um2, um3 = _read_initial_rows(jsonl)
    _write_case(
        case_path=CASES_DIR / "15_evaluator__pre_submit_evaluation_failure.md",
        title="evaluator — attempt 1 (proceeds to `submit_evaluation_failure`); same user_msg_1 shape as a passing evaluator, captured for completeness on the evaluator-failure path",
        source=f"pipeline.attempt_retry_evaluator_failure/{run.name}/{rel}",
        notes=(
            "Closes Gap 5 in the original gap report. The evaluator's *input* shape is "
            "identical regardless of the verdict the evaluator decides to submit — "
            "the failure path is the agent's behavior (`submit_evaluation_failure` "
            "with `summary` + `failed_criteria`), not a context-engine branch. This "
            "case documents the input prompt that precedes such a decision so readers "
            "can audit the full failure path alongside cases 07 (partial-success) and "
            "08 (complete-success). The downstream effect of `submit_evaluation_failure` "
            "is what case 13's planner sees as the rich `<attempt status=\"failed\">` block."
        ),
        system=system,
        user_msg_1=um1,
        user_msg_2=um2,
        user_msg_3=um3,
    )
    print(f"wrote {CASES_DIR}/15_evaluator__pre_submit_evaluation_failure.md")


def main() -> int:
    CASES_DIR.mkdir(parents=True, exist_ok=True)
    case_12_planner_child_workflow_close_only()
    case_13_planner_after_evaluator_failure()
    case_14_executor_with_dependency_results()
    case_15_evaluator_pre_failure_submission()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
