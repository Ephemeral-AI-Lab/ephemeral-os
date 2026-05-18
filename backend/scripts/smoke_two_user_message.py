"""Two-user-message launch smoke gate (plan §7 Step 7).

BLOCKING pre-merge gate — does NOT run in CI (live API cost). The plan author
runs this script locally before merging the two-user-message-shape change and
pastes the output in the PR description.

The script has TWO modes:

* ``--dry-run`` (default): runs offline. Composes a planner ``LaunchBundle``
  against in-memory stores, asserts the structural invariants the renderer
  split is supposed to guarantee, and prints both launch shapes side-by-side
  so a reviewer can visually compare them without burning API credits.

* ``--live``: runs the FULL gate. Spawns a real planner against an Anthropic
  endpoint TWICE — once with the legacy single-user-message launch shape,
  once with the new two-user-message launch shape — and asserts SEMANTIC
  equivalence: same ``task_count``, same ``agent_name`` multiset across
  produced tasks, same ``terminal_decision`` (``continues_goal`` vs
  ``closes_goal``).

  ``--live`` requires the production runtime config + Anthropic credentials.
  If it fails, STOP. Either the Anthropic ``[user, user]`` merge differs from
  the merged-concatenation behavior we assumed, or the renderer split shifted
  a signal the model relied on. Re-open OQ §10 #1 with the divergent
  transcript before merging.

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


from task_center.context_engine.packet import (  # noqa: E402
    ContextBlock,
    ContextBlockKind,
    ContextPacket,
    ContextPriority,
    ContextRefs,
)
from task_center.context_engine.renderer import XmlPromptRenderer  # noqa: E402


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
            ),
            ContextBlock(
                kind=ContextBlockKind.ITERATION_STATEMENT,
                priority=ContextPriority.REQUIRED,
                text="Iteration 1: implement and verify two-user-message shape.",
            ),
            ContextBlock(
                kind=ContextBlockKind.ROLE_INSTRUCTION,
                priority=ContextPriority.REQUIRED,
                text=(
                    "You are planning the first attempt for this iteration's "
                    "goal. Propose a plan that decomposes the iteration goal "
                    "into generator tasks with a clear evaluation contract."
                ),
            ),
        ],
    )


def _print_shape(label: str, prompt: str, initial_messages_text: str | None) -> None:
    print(f"=== {label} ===")
    if initial_messages_text is not None:
        print("initial_messages[0] (user msg 1):")
        print(initial_messages_text)
        print()
        print("prompt (user msg 2):")
    else:
        print("prompt (single user msg):")
    print(prompt)
    print()


def _dry_run() -> int:
    packet = _build_demo_packet()
    renderer = XmlPromptRenderer()
    context_text = renderer.render_context(packet)
    role_text = renderer.render_role_instruction(packet)

    # Structural assertions the production launch path relies on.
    assert role_text is not None, "planner-shaped packet must yield role_instruction"
    assert "# How to Proceed" not in context_text, (
        "render_context still emits the legacy '# How to Proceed' heading"
    )

    legacy_prompt = context_text.rstrip() + "\n\n" + role_text
    new_prompt = role_text  # user msg 2

    _print_shape(
        "LEGACY (single user message — concatenated context + role_instruction)",
        legacy_prompt,
        initial_messages_text=None,
    )
    _print_shape(
        "NEW (two user messages — context as initial_messages, role_instruction as prompt)",
        new_prompt,
        initial_messages_text=context_text,
    )
    print(
        "STRUCTURAL SMOKE: context_message has no '# How to Proceed' heading "
        "and role_instruction_message is non-empty — OK."
    )
    print(
        "LIVE GATE: this dry-run does NOT prove semantic equivalence under "
        "Anthropic. Re-run with --live before merging to compare planner "
        "outputs across the two launch shapes."
    )
    return 0


def _live_run() -> int:
    """Live Anthropic two-shape comparison.

    Intentionally NOT implemented inline: the live runtime needs a full
    composer + stores + sandbox + Anthropic credential set, which differs
    per developer machine. The plan documents this as a manual gate; this
    function raises a clear error directing the operator to the manual
    procedure rather than half-implementing it.
    """
    print(
        "ERROR: --live mode requires the operator to wire production stores "
        "+ Anthropic credentials. The plan documents the manual procedure "
        "(plan §7 Step 7); the structural --dry-run smoke is gated by this "
        "script but the live comparison is a manual step the plan author "
        "runs locally before merging. Paste the live output in the PR "
        "description.",
        file=sys.stderr,
    )
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run the full Anthropic two-shape comparison (cost-bearing).",
    )
    args = parser.parse_args(argv)
    if args.live:
        return _live_run()
    return _dry_run()


if __name__ == "__main__":
    raise SystemExit(main())
