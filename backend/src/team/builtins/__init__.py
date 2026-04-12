"""Builtin team-mode agent definitions and internal runtime helpers.

Definitions live as Markdown+YAML-frontmatter files in this package's
directory.  ``register_all()`` loads them at boot, seeds the database,
and populates the in-memory registry.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from agents.loader import load_agents_dir
from agents.registry import register_definition
from agents.types import AgentDefinition

if TYPE_CHECKING:
    from agents.db.store import AgentDefinitionStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical name constants — imported across the codebase for dispatch logic.
# ---------------------------------------------------------------------------
TEAM_PLANNER = "team_planner"
DEVELOPER = "developer"
VALIDATOR = "validator"
SCOUT = "scout"
TEAM_REPLANNER = "team_replanner"

_BUILTINS_DIR = Path(__file__).resolve().parent / "agents"

# Expected number of builtin agents.  If a seed file fails to parse,
# ``load_agents_dir`` silently skips it — this constant lets us detect
# that early rather than discovering a missing agent at dispatch time.
_EXPECTED_BUILTIN_COUNT = 5


def _load_builtin_definitions() -> list[AgentDefinition]:
    """Load all builtin agent definitions from the seed files."""
    defs = load_agents_dir(_BUILTINS_DIR)
    # Override source to "builtin" — load_agents_dir defaults to "user".
    for d in defs:
        d.source = "builtin"  # type: ignore[misc]
    if len(defs) != _EXPECTED_BUILTIN_COUNT:
        logger.error(
            "Expected %d builtin agents but loaded %d from %s — "
            "check seed files for parse errors",
            _EXPECTED_BUILTIN_COUNT,
            len(defs),
            _BUILTINS_DIR,
        )
    return defs


def register_all(*, store: "AgentDefinitionStore | None" = None) -> None:
    """Register all builtin team agents.

    When *store* is provided, each definition is seeded into the database
    first (skipped if already present), then loaded from DB into the
    in-memory registry.  This lets users customise builtins via the DB
    while keeping a code-level fallback for environments without a DB.
    """
    defaults = _load_builtin_definitions()

    if store is not None:
        from agents.builder.service import AgentBuilderService

        for defn in defaults:
            record = store.seed_builtin(defn)
            loaded = AgentBuilderService.record_to_definition(record)
            register_definition(loaded)
    else:
        for defn in defaults:
            register_definition(defn)

    logger.info("team builtins registered (%d agents, db=%s)", len(defaults), store is not None)
