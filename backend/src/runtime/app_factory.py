"""Runtime configuration + store singletons for non-server entrypoints.

Resurrects the runtime surface from the pre-deletion ``server.app_factory``
(commit 9969f891^). HTTP routers and the FastAPI lifespan plumbing are not
restored — only the symbols that production code still imports lazily:

* :class:`RuntimeConfig` — durable runtime config consumed by
  ``engine.agent.factory`` and ``task_center.agent_launch.launcher``.
* Module-level store singletons (``task_center_store``, ``agent_run_store``,
  ``model_store``).
* :func:`ensure_runtime_stores_ready` — idempotent bootstrap that initialises
  the singletons against the project SQLAlchemy session factory and seeds the
  model registry from JSON.

The 3-field :class:`RuntimeConfig` shape is intentional: ``system_prompt_override``
was empirically dead (grep finds zero non-test callers in HEAD) and is left
out. Agent system prompts come from the markdown profile frontmatter
(``backend/src/agents/profile/main/*.md`` → ``system_prompt:`` field), not from
runtime overrides.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from config.settings import Settings, load_settings
from db.stores.agent_run_store import AgentRunStore
from db.stores.model_store import ModelStore
from db.stores.task_center_store import TaskCenterStore

if TYPE_CHECKING:
    from providers.types import SupportsStreamingMessages

logger = logging.getLogger(__name__)


@dataclass
class RuntimeConfig:
    """Durable runtime configuration shared by request-scoped agents.

    Read by ``engine.agent.factory`` (cwd, external_api_client, resolve_settings)
    and ``task_center.agent_launch.launcher``. ``_initial_messages`` is part of
    the resurrected public shape; ``system_prompt_override`` is intentionally
    absent — see module docstring.
    """

    cwd: str
    external_api_client: "SupportsStreamingMessages | None" = None
    _initial_messages: list[dict] | None = field(default=None, repr=False)

    def resolve_settings(self) -> Settings:
        """Load Settings as-is. Agent system prompts come from agent profile
        markdown frontmatter, not from a runtime override."""
        return load_settings()


# ---------------------------------------------------------------------------
# Store singletons — initialised lazily via ensure_runtime_stores_ready.
# ---------------------------------------------------------------------------

task_center_store = TaskCenterStore()
agent_run_store = AgentRunStore()
model_store = ModelStore()


def _model_registry_path() -> Path:
    """Path to the JSON model registry seed.

    Recovered verbatim from pre-deletion ``server/app_factory.py``. Resolves
    to ``<repo>/models/registry.json``. With ``__file__`` at
    ``backend/src/runtime/app_factory.py``, four ``.parent`` hops walk
    ``backend/src/runtime/`` → ``backend/src/`` → ``backend/`` → repo root.
    """
    return Path(__file__).resolve().parent.parent.parent.parent / "models" / "registry.json"


def ensure_runtime_stores_ready(settings: "Settings | None" = None):
    """Initialise the runtime store singletons + seed the model registry.

    Idempotent. Returns the bound ``sessionmaker`` once stores are ready, or
    ``None`` when running without a database (file-only fallback preserved
    from the pre-deletion behaviour).
    """
    from db.engine import get_session_factory, initialize_db

    settings = settings or load_settings()
    sf = get_session_factory()
    if sf is None:
        sf = initialize_db(settings.database)
    if sf is None:
        logger.info("Running without database — file-based persistence only")
        return None

    if not task_center_store.is_ready:
        task_center_store.initialize(sf)
    if not agent_run_store.is_ready:
        agent_run_store.initialize(sf)
    if not model_store.is_available:
        model_store.initialize(sf)

    registry_path = _model_registry_path()
    assert registry_path.is_file(), (
        f"Model registry JSON not found at {registry_path}. "
        "Recover via git show 9969f891^:backend/src/server/app_factory.py "
        "for the original path resolution."
    )
    model_store.seed_from_json(str(registry_path))
    return sf


__all__ = [
    "RuntimeConfig",
    "agent_run_store",
    "ensure_runtime_stores_ready",
    "model_store",
    "task_center_store",
]
