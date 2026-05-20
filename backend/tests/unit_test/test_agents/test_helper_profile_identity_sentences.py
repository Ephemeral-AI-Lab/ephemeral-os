"""Pin the identity sentences in helper profile bodies.

Under the two-user-message launch shape, the helper profile body is loaded
into ``AgentDefinition.system_prompt`` and becomes the system prompt for the
run. The identity sentence is the only signal that distinguishes one helper
from another (everything else travels in user msgs 1/2 per call), so a
regression to a placeholder body silently breaks identity.

If you intentionally change the identity wording, update these pins.
"""

from __future__ import annotations

from pathlib import Path

_BACKEND_SRC = Path(__file__).resolve().parents[3] / "src"


def _read_profile(profile_subpath: str) -> str:
    return (_BACKEND_SRC / "agents" / "profile" / profile_subpath).read_text()


def test_advisor_profile_body_contains_identity_sentence():
    body = _read_profile("helper/advisor.md")
    assert "You are an advisor agent." in body


def test_resolver_profile_body_contains_identity_sentence():
    body = _read_profile("helper/resolver.md")
    assert "You are the resolver helper agent." in body


# ---------------------------------------------------------------------------
# Main-agent + subagent identity-sentence pins (Phase 2 — P2.1).
#
# Under the two-user-message launch shape, the profile body becomes the
# system prompt. A regression to a placeholder body (e.g. "(Role instruction
# is injected dynamically...)") would silently destroy identity for these
# agents. Each pin is the byte-identical identity sentence on disk today;
# a single-word change in the .md fails the test on the same PR.
#
# executor.md is intentionally NOT pinned: it is a variant-only entry-point
# with no body sentence (delegates to executor_success_failure /
# executor_success_handoff). Pinning it would force adding a fake body.
# ---------------------------------------------------------------------------

_MAIN_AGENT_IDENTITY_PINS = {
    "main/entry_executor.md": (
        "You are the **entry executor** — the agent that receives "
        "the top-level user request."
    ),
    "main/evaluator.md": "You are the **main-agent evaluator**.",
    "main/executor_success_failure.md": (
        "You are the **main-agent generator executor** at a leaf depth "
        "— no further delegation is allowed."
    ),
    "main/executor_success_handoff.md": (
        "You are the **main-agent generator executor** at a depth where "
        "handoff is still available."
    ),
    "main/generator_verifier.md": "You are the **main-agent generator verifier**.",
    "main/planner_closes_or_defers.md": (
        "You are the **planner** for one attempt in the TaskCenter harness."
    ),
    "main/planner_closes_goal.md": (
        "You are the **planner** for one attempt in the TaskCenter harness."
    ),
    "subagent/explorer.md": "You are the explorer subagent.",
}


def test_main_agent_profile_bodies_contain_identity_sentence():
    missing: list[str] = []
    for subpath, pin in _MAIN_AGENT_IDENTITY_PINS.items():
        body = _read_profile(subpath)
        if pin not in body:
            missing.append(f"{subpath}: missing pinned sentence {pin!r}")
    assert not missing, (
        "Profile bodies regressed away from pinned identity sentences:\n"
        + "\n".join(missing)
    )
