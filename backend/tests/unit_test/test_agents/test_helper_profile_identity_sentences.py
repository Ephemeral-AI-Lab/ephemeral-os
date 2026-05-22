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
# executor.md is pinned because it is now the concrete generator executor.
# ---------------------------------------------------------------------------

_MAIN_AGENT_IDENTITY_PINS = {
    "main/evaluator.md": "You are the **main-agent evaluator**.",
    "main/executor.md": "You are the **main-agent generator executor**.",
    "main/generator_verifier.md": "You are the **main-agent generator verifier**.",
    "main/planner.md": (
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
