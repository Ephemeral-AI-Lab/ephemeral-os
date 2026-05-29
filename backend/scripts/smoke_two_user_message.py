"""Per-agent launch-message shape smoke gate (plan v3.3 §7 Step 7).

BLOCKING pre-merge gate — does NOT run in CI (live API cost). The plan author
runs this script locally before merging the multi-row launch-message refactor
and pastes the output in the PR description.

Mode:

* ``--dry-run`` (default): runs offline. Composes a planner
  ``AgentEntryMessages`` against in-memory stores, asserts the structural
  invariants the wire shape is supposed to guarantee, and prints the four
  rows so a reviewer can visually inspect them without burning API credits.

* ``--live``: documented as a manual procedure — the live runtime needs a
  full composer + stores + sandbox + Anthropic credential set, which differs
  per developer machine. This script gates the structural smoke; the live
  comparison is a manual step the plan author runs locally.

Run examples::

    .venv/bin/python -m backend.scripts.smoke_two_user_message
    .venv/bin/python -m backend.scripts.smoke_two_user_message --live
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running as a script (``python smoke_two_user_message.py``) from any
# cwd: add backend/src to sys.path so the production imports resolve.
_BACKEND_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(_BACKEND_SRC))


from task_center.agent_launch.skill_message import _wrap_task_guidance  # noqa: E402
from task_center.context_engine.packet import (  # noqa: E402
    ContextBlock,
    ContextBlockKind,
    ContextPacket,
    ContextPriority,
    ContextRefs,
)
from task_center.context_engine.renderer import XmlPromptRenderer  # noqa: E402
from task_center.context_engine.task_guidance import (  # noqa: E402
    build_task_guidance,
)


def _build_demo_packet() -> ContextPacket:
    """Build a minimal planner packet so the renderer split is exercised."""
    return ContextPacket(
        target_role="planner",
        canonical_refs=ContextRefs(
            goal_id="goal-demo",
            iteration_id="iter-demo",
            attempt_id="attempt-demo",
        ),
        blocks=[
            ContextBlock(
                kind=ContextBlockKind.GOAL_STATEMENT,
                priority=ContextPriority.REQUIRED,
                text="Ship the smoke-test gate as a runnable script.",
                metadata={"tag": "goal"},
            ),
            ContextBlock(
                kind=ContextBlockKind.ITERATION_STATEMENT,
                priority=ContextPriority.REQUIRED,
                text="(identical to &lt;goal&gt;)",
                metadata={
                    "group_id": "iteration_1_current",
                    "group_tag": "iteration",
                    "group_attrs": 'iteration_no="1" status="current"',
                    "child_tag": "iteration_goal",
                    "iteration_no": "1",
                },
            ),
        ],
    )


def _make_planner_def():
    from agents import AgentDefinition, AgentKind

    return AgentDefinition(
        name="planner",
        description="planner",
        agent_kind=AgentKind.PLANNER,
        context_recipe="planner",
        terminals=["submit_plan_closes_goal", "submit_plan_defers_goal"],
    )


def _print_shape(label: str, lines: list[str]) -> None:
    print(f"=== {label} ===")
    for ln in lines:
        print(ln)
    print()


def _dry_run() -> int:
    packet = _build_demo_packet()
    renderer = XmlPromptRenderer()
    body = renderer.render_context(packet)
    context_message = "<context>\n" + body + "</context>\n"

    agent_def = _make_planner_def()
    prose = build_task_guidance(
        agent_def=agent_def, packet=packet, scope=None  # type: ignore[arg-type]
    )
    task_guidance = _wrap_task_guidance(prose, agent_def)

    # Structural assertions the production launch path relies on.
    assert context_message.startswith("<context>\n"), (
        "row-2 context envelope must start with '<context>\\n'"
    )
    assert context_message.rstrip().endswith("</context>"), (
        "row-2 context envelope must end with '</context>'"
    )
    assert task_guidance is not None, (
        "planner-shaped packet must yield a task_guidance row"
    )
    assert task_guidance.startswith("<Task Guidance>\n"), (
        "row-3 task_guidance envelope must start with '<Task Guidance>\\n'"
    )
    assert task_guidance.rstrip().endswith("</Task Guidance>"), (
        "row-3 task_guidance envelope must end with '</Task Guidance>'"
    )
    assert task_guidance.count("<terminal_tool_selection>") == 1, (
        "row-3 must include exactly one <terminal_tool_selection> block"
    )

    _print_shape(
        "ROW 2 (user msg 1 — <context> envelope)",
        [context_message],
    )
    _print_shape(
        "ROW 3 (user msg 2 — <Task Guidance> envelope with <terminal_tool_selection>)",
        [task_guidance],
    )
    print(
        "STRUCTURAL SMOKE: <context> envelope OK, <Task Guidance> envelope "
        "OK, <terminal_tool_selection> count == 1 — OK."
    )
    print(
        "LIVE GATE: this dry-run does NOT prove semantic equivalence under "
        "the model provider. Re-run with --live before merging to compare "
        "planner outputs across the two launch shapes."
    )
    return 0


def _live_run() -> int:
    """Live model-provider comparison.

    Intentionally NOT implemented inline: the live runtime needs a full
    composer + stores + sandbox + Anthropic credential set, which differs
    per developer machine. The plan documents this as a manual gate; this
    function raises a clear error directing the operator to the manual
    procedure rather than half-implementing it.
    """
    print(
        "ERROR: --live mode requires the operator to wire production stores "
        "+ Anthropic credentials. The plan documents the manual procedure; "
        "the structural --dry-run smoke is gated by this script but the "
        "live comparison is a manual step the plan author runs locally "
        "before merging. Paste the live output in the PR description.",
        file=sys.stderr,
    )
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run the full live-provider two-shape comparison (cost-bearing).",
    )
    args = parser.parse_args(argv)
    if args.live:
        return _live_run()
    return _dry_run()


if __name__ == "__main__":
    raise SystemExit(main())
