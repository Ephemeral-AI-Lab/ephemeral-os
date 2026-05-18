"""Generate cases 13/14/15 from existing scenario runs that close
the previously-flagged content gaps in the initial_messages_cases
directory.

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
BACKEND_RUNS = REPO / "backend" / ".sweevo_runs" / "scenario_logs"


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
    base = BACKEND_RUNS / scenario
    runs = sorted(base.iterdir(), reverse=True)
    if not runs:
        raise SystemExit(f"no runs under {base}")
    return runs[0]


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
            "", "## user_msg_3 — row 4 (skill + terminal_selection)", "", "```", user_msg_3.rstrip(), "```",
        ])
    parts.append("")
    case_path.write_text("\n".join(parts))


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
        title="planner — iteration 1, attempt 2 (after evaluator failure; rich `<attempt status=\"failed\">` body with real plan_spec, real generator outcomes, real evaluator judgment)",
        source=f"pipeline.attempt_retry_evaluator_failure/{run.name}/{rel}",
        notes=(
            "Closes Gap 2 in the original gap report. Unlike case 03 "
            "(failed at planner-validation, so `<plan_spec>(not submitted)</plan_spec>` "
            "and `(no generator tasks recorded)`), here the prior attempt's plan was "
            "valid, executor ran, evaluator returned `submit_evaluation_failure` — so "
            "the `<attempt status=\"failed\">` block now carries real bodies for "
            "`<plan_spec>`, `<generator_outcomes>` (with `<status_summary>` + per-task "
            "`<task status>`), and `<evaluator_judgment status=\"ran\" verdict=\"fail\">` "
            "(with `<evaluation_criteria>`, `<evaluator_summary>`, `<failed_criteria>`)."
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
        title="executor — dependency_results branch (generator task `b`, deps: [`a`]); user_msg_1 carries a real `<dependency_results>` block",
        source=f"pipeline.dependency_dag_serial/{run.name}/{rel}",
        notes=(
            "Closes Gap 3 in the original gap report. The scenario submits a serial "
            "DAG `a → b → c`; task `b` runs with `deps=[\"a\"]`, so its composer "
            "renders the `<dependency_results>` group (one `<dependency id=...>` "
            "child per upstream task) between `<attempt_plan>` and `<assigned_task>`. "
            "The role_instruction (row 3) is the `has_deps=True` branch of "
            "`generator_instruction`, opening with \"This task has dependencies on "
            "other generator tasks…\". This is the variant the existing initial_messages "
            "scenario could not exercise because its plans only have single-task DAGs."
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
    case_13_planner_after_evaluator_failure()
    case_14_executor_with_dependency_results()
    case_15_evaluator_pre_failure_submission()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
