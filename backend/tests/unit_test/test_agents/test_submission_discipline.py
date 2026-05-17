"""Static check that every main-agent profile MD carries the §4.5 paragraph.

Pre-mortem §7 scenario 2 of the advisor-loop redesign plan: a new
main-agent profile (or an edit) without the submission-discipline
paragraph leaves the advisor's verdict non-binding. CI must fail loudly.
"""

from __future__ import annotations

import pathlib

import pytest


_BACKEND_SRC = pathlib.Path(__file__).resolve().parents[3] / "src"
_MAIN_PROFILE_DIR = _BACKEND_SRC / "agents" / "profile" / "main"


# Discriminating sentences from §4.5 of the advisor-loop-and-helper-context-
# redesign plan. Match on these specific phrases (not the full paragraph) so
# minor wording or formatting drift in the surrounding markdown does not
# silently bypass the gate.
_REQUIRED_FRAGMENTS = (
    "Submission discipline",
    "ask_advisor",
    'verdict `"approve"`',
    'verdict `"reject"`',
    "Submit exactly one terminal tool per run.",
)


def _main_profile_mds() -> list[pathlib.Path]:
    """Profiles that should carry the discipline — exclude pure routing thin entry points."""
    out: list[pathlib.Path] = []
    for md_path in _MAIN_PROFILE_DIR.glob("*.md"):
        text = md_path.read_text(encoding="utf-8")
        # ``executor.md`` is a thin routing entry point with empty body — its
        # variant targets carry the discipline.
        if md_path.name == "executor.md":
            continue
        if "agent_kind" not in text:
            continue
        out.append(md_path)
    return out


@pytest.mark.parametrize(
    "md_path", _main_profile_mds(), ids=lambda p: p.name
)
def test_main_profile_has_submission_discipline(md_path: pathlib.Path) -> None:
    text = md_path.read_text(encoding="utf-8")
    for fragment in _REQUIRED_FRAGMENTS:
        assert fragment in text, (
            f"{md_path.name!r} is missing required submission-discipline "
            f"fragment {fragment!r}. Add the §4.5 paragraph from the "
            f"advisor-loop redesign plan."
        )
