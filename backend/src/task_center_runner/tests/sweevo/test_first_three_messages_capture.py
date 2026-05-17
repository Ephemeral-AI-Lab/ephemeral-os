"""Live regression for the first-three-messages capture scenario.

Runs ``pipeline.first_three_messages_capture`` with the standard SWE-EVO
sandbox + stores fixtures, then asserts the captured ``message.jsonl``
trees carry the right shape for every iteration position and attempt and
emits ``first_three_messages_report.md`` next to the audit run directory.

For helper (advisor / resolver) and subagent (explorer) first-message
construction, see ``scripts/build_first_three_messages_report.py`` — the
mock-runner does not invoke helpers today, so the report builder calls the
real builder functions in ``tools/ask_helper/_lib/_compose.py`` and
``task_center/context_engine/recipes/role_instruction.py`` against a
realistic parent context. Once ``MockSquadRunner`` grows a helper dispatch,
this test should be extended to also collect ``advisor`` / ``resolver`` /
``explorer`` ``message.jsonl`` trees from the live run.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

import pytest

from benchmarks.sweevo.models import SWEEvoInstance
from task_center_runner.audit.events import EventType
from task_center_runner.benchmarks.sweevo.fixtures import run_sweevo_scenario
from task_center_runner.core.stores import TaskCenterStoreBundle
from task_center_runner.scenarios import SCENARIO_REGISTRY


pytestmark = pytest.mark.asyncio


_SCENARIO_NAME = "pipeline.first_three_messages_capture"


@pytest.mark.skipif(
    not os.environ.get("EPHEMERALOS_DATABASE_URL"),
    reason="EPHEMERALOS_DATABASE_URL not set - task_center_runner requires PostgreSQL",
)
async def test_first_three_messages_capture(
    sweevo_instance: SWEEvoInstance,
    workspace: dict[str, object],
    audit_dir: Path,
    stores: TaskCenterStoreBundle,
) -> None:
    scenario = SCENARIO_REGISTRY[_SCENARIO_NAME]()
    report = await run_sweevo_scenario(
        scenario,
        instance=sweevo_instance,
        sandbox_id=str(workspace["sandbox_id"]),
        audit_dir=audit_dir,
        stores=stores,
    )

    # 1) Goal closes succeeded, 2 iterations, 3 attempts total
    #    (iter1 attempts 1 and 2; iter2 attempt 1).
    assert report.task_center_status == "done", report.metrics
    goal = report.graph_summary["goals"][0]
    assert goal["status"] == "succeeded"
    assert len(goal["iterations"]) == 2, goal
    attempts = [
        attempt
        for iteration in goal["iterations"]
        for attempt in iteration["attempts"]
    ]
    assert len(attempts) == 3, attempts

    counts = Counter(event.type for event in report.events)
    assert counts[EventType.PLANNER_INVOKED] >= 3, counts
    assert counts[EventType.PLANNER_PARTIAL_PLAN] == 1, counts
    assert counts[EventType.PLANNER_FULL_PLAN] == 1, counts
    assert counts[EventType.TOOL_CALL_ERROR] >= 1, counts
    assert counts[EventType.EVALUATOR_SUCCESS] == 2, counts

    # 2) message.jsonl present for every main-agent role we care about.
    messages = list(report.run_dir.rglob("message.jsonl"))
    assert messages, f"no message.jsonl under {report.run_dir}"

    captured = {}
    for path in messages:
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        if len(rows) < 2:
            continue
        role_dir = path.parent.name
        captured[str(path.relative_to(report.run_dir))] = {
            "role_dir": role_dir,
            "system": _text_of(rows[0]),
            "user_msg_1": _text_of(rows[1]),
        }
    assert captured, "no agent captures harvested"

    # 3) For every main-agent capture, the presence contract holds.
    for rel, cap in captured.items():
        role_dir = cap["role_dir"]
        system = cap["system"]
        um1 = cap["user_msg_1"]
        assert system.strip(), f"{rel}: empty system prompt"
        assert um1.strip(), f"{rel}: empty user_msg_1"
        if "planner" in role_dir:
            assert "# Goal" in um1, f"{rel}: missing goal block"
            assert (
                "# Current Iteration" in um1
                or "Goal / Current Iteration" in um1
            ), f"{rel}: missing iteration block"
        elif "executor" in role_dir and not role_dir.startswith("entry_executor"):
            assert (
                "Attempt Plan" in um1 or "Assigned Task" in um1
            ), f"{rel}: missing attempt plan / assigned task"
        elif "evaluator" in role_dir:
            assert (
                "Evaluation Criteria" in um1
            ), f"{rel}: missing evaluation criteria"

    # 4) Emit the markdown report next to the run.
    report_path = report.run_dir / "first_three_messages_report.md"
    _write_report(report.run_dir, captured, report_path)
    assert report_path.exists()


def _text_of(row: dict) -> str:
    parts: list[str] = []
    for block in row.get("content", []) or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts)


def _write_report(run_dir: Path, captured: dict, dest: Path) -> None:
    lines: list[str] = []
    lines.append("# First-Three-Messages Capture — Live Run\n")
    lines.append(f"Source run directory: `{run_dir}`\n")
    lines.append(
        "Two rows per agent (system + composer's combined user message). "
        "Helpers (advisor / resolver) and subagent (explorer) are constructed "
        "by `scripts/build_first_three_messages_report.py` — see "
        "`docs/reports/first_three_messages_report.md`.\n"
    )
    for rel, cap in sorted(captured.items()):
        lines.append(f"## `{rel}`\n")
        lines.append("**system**\n")
        lines.append(f"```\n{cap['system'].strip()[:6000]}\n```\n")
        lines.append("**user_msg_1**\n")
        lines.append(f"```\n{cap['user_msg_1'].strip()[:6000]}\n```\n")
    dest.write_text("\n".join(lines) + "\n")
