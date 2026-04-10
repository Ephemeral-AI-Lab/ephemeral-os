"""Tier 1 — project-level context for a TeamRun."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from team.models import Briefing


@dataclass
class ProjectContext:
    goal: str = ""
    user_request: str = ""
    rationale_history: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # Run-scoped shared briefings (§13). Keyed by canonical_scope; written
    # explicitly via the ``share_briefing`` tool, read automatically by
    # ``render_briefings`` for every executor and spawned subagent.
    shared_briefings: dict[str, Briefing] = field(default_factory=dict)
    # Runtime-owned scout scopes that may be displaced under briefing
    # pressure. Explicit promotions remove a scope from this set.
    auto_promoted_scout_scopes: set[str] = field(default_factory=set)
    # Stable scout replacement metadata keyed by canonical scope. Kept in
    # run memory so equal/missing snapshot ties do not degrade to
    # last-writer-wins.
    stable_scout_versions: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Phase 2 — project identity for the persistent atlas. Both fields
    # default to empty strings; atlas tools treat an empty ``project_key``
    # as "atlas disabled" and degrade gracefully.
    project_key: str = ""
    repo_root: str = ""

    def add_rationale(self, text: str) -> None:
        if text:
            self.rationale_history.append(text)

    def add_note(self, text: str) -> None:
        if text:
            self.notes.append(text)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "user_request": self.user_request,
            "rationale_history": list(self.rationale_history),
            "notes": list(self.notes),
            "project_key": self.project_key,
            "repo_root": self.repo_root,
            "shared_briefings": {
                scope: {
                    "name": b.name,
                    "source": b.source,
                    "ref": b.ref,
                    "inline": b.inline,
                    "description": b.description,
                }
                for scope, b in self.shared_briefings.items()
            },
            "auto_promoted_scout_scopes": sorted(self.auto_promoted_scout_scopes),
            "stable_scout_versions": {
                scope: dict(version)
                for scope, version in self.stable_scout_versions.items()
            },
        }
