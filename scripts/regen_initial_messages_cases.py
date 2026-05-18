"""One-off regenerator for docs/reports/initial_messages_cases/*.

Reads the message.jsonl captures from the most recent
``pipeline.initial_messages_capture`` run under
``backend/.sweevo_runs/scenario_logs/`` and writes one .md file per
agent role/iteration/attempt position, matching the historical naming
convention. Wire shape (post-v3.3): user_msg_1 is the ``<context>``
envelope around the rendered packet, user_msg_2 is the
``<Task Guidance>`` envelope with embedded ``<terminal_tool_selection>``,
and user_msg_3 (planner only) is the skill row with its own
``<terminal_tool_selection>``.

Also produces the helper / subagent cases (09 advisor, 10 resolver, 11
explorer) by calling the real builder code in
``tools/ask_helper/_lib/_compose.py`` and
``task_center/task_guidance/builders.py`` against a representative
parent context lifted from the same live capture.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path("/Users/yifanxu/machine_learning/LoVC/EphemeralOS")
SRC = REPO / "backend" / "src"
sys.path.insert(0, str(SRC))

from agents import get_definition, load_agents_tree, register_definition

for _ad in load_agents_tree(SRC / "agents" / "profile"):
    register_definition(_ad)

from task_center.task_guidance.builders import build_explorer_task_guidance
from tools.ask_helper._lib._compose import HelperMessages, assemble_user_msg_1
from tools.ask_helper.ask_advisor import _build_advisor_user_msg_2
from tools.ask_helper.ask_resolver import _build_resolver_user_msg_2

CASES_DIR = REPO / "docs" / "reports" / "initial_messages_cases"


def _text_of(row: dict) -> str:
    parts: list[str] = []
    for block in row.get("content", []) or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts)


def _latest_run() -> Path:
    """Return the newest scenario run dir across known audit base dirs.

    The audit base is picked at fixture-resolution time; pytest invoked from
    the repo root writes to ``<repo>/.sweevo_runs`` while older runs may sit
    under ``backend/.sweevo_runs``. Scan both and pick the most recent.
    """
    candidates = [
        REPO / ".sweevo_runs" / "scenario_logs" / "pipeline.initial_messages_capture",
        REPO / "backend" / ".sweevo_runs" / "scenario_logs" / "pipeline.initial_messages_capture",
    ]
    runs: list[Path] = []
    for base in candidates:
        if base.is_dir():
            runs.extend(p for p in base.iterdir() if p.is_dir())
    if not runs:
        raise SystemExit(f"No runs under {candidates!r}")
    return max(runs, key=lambda p: p.stat().st_mtime)


def _agents_under(run_dir: Path) -> dict[str, tuple[str, str, Path]]:
    """Map role_dir → (iteration_dir, attempt_dir, message.jsonl path)."""
    out: dict[str, tuple[str, str, Path]] = {}
    for jsonl in sorted(run_dir.rglob("message.jsonl")):
        rel = jsonl.relative_to(run_dir).parts
        iteration = next((p for p in rel if p.startswith("iteration_")), "")
        attempt = next((p for p in rel if p.startswith("attempt_")), "")
        role_dir = rel[-2]
        out[str(jsonl.relative_to(run_dir))] = (iteration, attempt, jsonl)
    return out


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
    system = _text_of(rows[0]) if rows and rows[0].get("role") == "system" else ""
    um1 = _text_of(rows[1]) if len(rows) > 1 and rows[1].get("role") == "user" else ""
    um2 = _text_of(rows[2]) if len(rows) > 2 and rows[2].get("role") == "user" else ""
    um3 = _text_of(rows[3]) if len(rows) > 3 and rows[3].get("role") == "user" else ""
    return system, um1, um2, um3


def _write_case(
    *,
    case_path: Path,
    title: str,
    source: str,
    system: str,
    user_msg_1: str,
    user_msg_2: str = "",
    user_msg_3: str = "",
) -> None:
    parts: list[str] = []
    parts.append(f"# {title}")
    parts.append(f"- source: `{source}`")
    parts.append("")
    parts.append("## system")
    parts.append("")
    parts.append("```")
    parts.append(system.rstrip())
    parts.append("```")
    parts.append("")
    parts.append("## user_msg_1")
    parts.append("")
    parts.append("```")
    parts.append(user_msg_1.rstrip())
    parts.append("```")
    if user_msg_2:
        parts.append("")
        parts.append("## user_msg_2")
        parts.append("")
        parts.append("```")
        parts.append(user_msg_2.rstrip())
        parts.append("```")
    if user_msg_3:
        parts.append("")
        parts.append("## user_msg_3 — row 4 (skill + terminal_tool_selection)")
        parts.append("")
        parts.append("```")
        parts.append(user_msg_3.rstrip())
        parts.append("```")
    parts.append("")
    case_path.write_text("\n".join(parts))


def regenerate_main_cases(run_dir: Path) -> None:
    agents = _agents_under(run_dir)

    case_specs: list[tuple[str, str, str, dict]] = []

    for rel, (iteration, attempt, jsonl) in agents.items():
        role_dir = rel.split("/")[-2]
        if role_dir.startswith("entry_executor"):
            case_specs.append((
                "01_entry_executor__root_delegation.md",
                "entry_executor — root delegation (single-user-message launch)",
                rel,
                dict(),
            ))
        elif "planner" in role_dir:
            if iteration.startswith("iteration_01") and attempt.startswith("attempt_01"):
                fname = "02_planner__iter1_attempt1__fresh_no_failed_attempts.md"
                title = "planner — iteration 1, attempt 1 (fresh; planner_instruction branch: iter==1, no failed attempts)"
            elif iteration.startswith("iteration_01") and attempt.startswith("attempt_02"):
                fname = "03_planner__iter1_attempt2__after_evaluator_failure.md"
                title = "planner — iteration 1, attempt 2 (after evaluator failure; planner_instruction branch: iter==1, has failed attempts with rich `<attempt status=\"failed\">` body — real `<plan_spec>`, `<generator_outcomes>`, `<evaluator_judgment status=\"ran\" verdict=\"fail\">`)"
            elif iteration.startswith("iteration_02") and attempt.startswith("attempt_01"):
                fname = "04_planner__iter2_attempt1__continuation_full.md"
                title = "planner — iteration 2, attempt 1 (continuation; planner_instruction branch: iter>1, no failed attempts)"
            else:
                continue
            case_specs.append((fname, title, rel, dict()))
        elif "executor" in role_dir:
            if iteration.startswith("iteration_01"):
                fname = "05_executor__iter1_attempt2__continuation_partial__handoff_variant.md"
                title = "executor — iteration 1, attempt 2 (continuation partial; routed to executor_success_handoff variant; generator_instruction: has_deps=False)"
            else:
                fname = "06_executor__iter2_attempt1__continuation_full__handoff_variant.md"
                title = "executor — iteration 2, attempt 1 (continuation full; routed to executor_success_handoff variant; generator_instruction: has_deps=False)"
            case_specs.append((fname, title, rel, dict()))
        elif "evaluator" in role_dir:
            if iteration.startswith("iteration_01"):
                fname = "07_evaluator__iter1_attempt2__partial_attempt.md"
                title = "evaluator — iteration 1, attempt 2 (evaluator_instruction branch: is_partial=True; partial plan boundary present)"
            else:
                fname = "08_evaluator__iter2_attempt1__complete_attempt.md"
                title = "evaluator — iteration 2, attempt 1 (evaluator_instruction branch: is_partial=False; complete plan attempt)"
            case_specs.append((fname, title, rel, dict()))

    for fname, title, rel, _extra in case_specs:
        jsonl = run_dir / rel
        system, um1, um2, um3 = _read_initial_rows(jsonl)
        _write_case(
            case_path=CASES_DIR / fname,
            title=title,
            source=rel,
            system=system,
            user_msg_1=um1,
            user_msg_2=um2,
            user_msg_3=um3,
        )
        print(f"wrote {CASES_DIR / fname}")


def _harvest_executor_capture(run_dir: Path) -> tuple[str, str, str]:
    """Return (executor system, executor user_msg_1, executor user_msg_2)
    from the first available live executor capture, to seed helper construction.
    """
    agents = _agents_under(run_dir)
    for rel, (it, att, jsonl) in agents.items():
        role_dir = rel.split("/")[-2]
        if "executor" in role_dir and "entry_executor" not in role_dir:
            system, um1, um2, _ = _read_initial_rows(jsonl)
            return system, um1, um2
    raise RuntimeError("no executor capture found")


def regenerate_helpers(run_dir: Path) -> None:
    advisor_def = get_definition("advisor")
    resolver_def = get_definition("resolver")
    explorer_def = get_definition("explorer")
    if advisor_def is None or resolver_def is None or explorer_def is None:
        raise RuntimeError("Missing helper/explorer agent definitions.")
    parent_def = get_definition("executor_success_handoff") or get_definition(
        "executor_success_failure"
    ) or get_definition("executor")

    _, parent_um1, parent_um2 = _harvest_executor_capture(run_dir)
    parent_transcript = (
        "(omitted for brevity — real transcripts include every tool call "
        "and result the parent emitted before submitting.)"
    )

    advisor_messages = HelperMessages(
        helper_agent_def=advisor_def,
        parent_agent_def=parent_def,
        parent_user_msg_1=parent_um1,
        parent_user_msg_2=parent_um2,
        parent_transcript=parent_transcript,
    )
    advisor_um1 = assemble_user_msg_1(advisor_messages)
    advisor_um2 = _build_advisor_user_msg_2(
        messages=advisor_messages,
        tool_name="submit_execution_success",
        tool_payload={
            "summary": "Workspace preflight completed.",
            "artifacts": [],
        },
    )
    _write_case(
        case_path=CASES_DIR / "09_advisor__executor_pre_submission.md",
        title="advisor — invoked by the executor before terminal submission "
        "(programmatic; built by tools/ask_helper/_lib/_compose.py)",
        source="programmatic construction",
        system=advisor_def.system_prompt or "",
        user_msg_1=advisor_um1,
        user_msg_2=advisor_um2,
    )
    print(f"wrote {CASES_DIR / '09_advisor__executor_pre_submission.md'}")

    resolver_messages = HelperMessages(
        helper_agent_def=resolver_def,
        parent_agent_def=parent_def,
        parent_user_msg_1=parent_um1,
        parent_user_msg_2=parent_um2,
        parent_transcript=parent_transcript,
    )
    resolver_um1 = assemble_user_msg_1(resolver_messages)
    resolver_um2 = _build_resolver_user_msg_2(
        issues_to_resolve=[
            "preflight artifact `.ephemeralos/sweevo-mock/probe.txt` not found",
            "git rev-parse --is-inside-work-tree returned non-zero",
        ],
        issue_context=(
            "Evaluator observed the listed issues while inspecting the "
            "preflight executor's reported artifacts."
        ),
    )
    _write_case(
        case_path=CASES_DIR / "10_resolver__verifier_evaluator_issues.md",
        title="resolver — invoked by the verifier/evaluator on issues "
        "(programmatic; built by tools/ask_helper/_lib/_compose.py + "
        "ask_resolver._build_resolver_user_msg_2)",
        source="programmatic construction",
        system=resolver_def.system_prompt or "",
        user_msg_1=resolver_um1,
        user_msg_2=resolver_um2,
    )
    print(f"wrote {CASES_DIR / '10_resolver__verifier_evaluator_issues.md'}")

    explorer_um1 = (
        "Inspect the repository layout under backend/src/task_center to "
        "list every module that registers a context-recipe id and report "
        "file paths plus line numbers."
    )
    explorer_um2 = build_explorer_task_guidance()
    _write_case(
        case_path=CASES_DIR / "11_explorer_subagent__run_subagent.md",
        title="explorer subagent — invoked via run_subagent "
        "(programmatic; user_msg_1 = parent's free-text prompt; "
        "user_msg_2 = build_explorer_task_guidance())",
        source="programmatic construction",
        system=explorer_def.system_prompt or "",
        user_msg_1=explorer_um1,
        user_msg_2=explorer_um2,
    )
    print(f"wrote {CASES_DIR / '11_explorer_subagent__run_subagent.md'}")


def main() -> int:
    CASES_DIR.mkdir(parents=True, exist_ok=True)
    run_dir = _latest_run()
    print(f"using run_dir: {run_dir}")
    regenerate_main_cases(run_dir)
    regenerate_helpers(run_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
