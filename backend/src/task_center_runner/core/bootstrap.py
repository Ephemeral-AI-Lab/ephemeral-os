"""One-shot runtime bootstrap for the real-agent live-e2e path.

Ensures the Daytona provider, the production runtime store singletons, and
the markdown-defined agent registry are all populated before
``start_task_center_run`` runs with ``runner=None`` (real LLM). The
scenario / mock path never invokes this — mocks register their own agents
via ``task_center_runner.agent.mock.definitions.registered_mock_agents``.

Idempotent. Safe to call from CLI startup and pytest fixtures alike.
"""

from __future__ import annotations

from pathlib import Path

_BOOTSTRAPPED = False

# ``__file__`` resolves to ``backend/src/task_center_runner/core/bootstrap.py``.
# ``parents[2]`` points at ``backend/src/``; the production agent definitions
# live under ``backend/src/agents/profile/``.
_PROFILE_ROOT = Path(__file__).resolve().parents[2] / "agents" / "profile"

# Names the launcher resolves via
# ``EphemeralAttemptAgentLauncher._resolve_agent_definition``. Markdown
# frontmatter ``name:`` fields under ``_PROFILE_ROOT/main/`` register these:
# planner.md, reducer.md, and executor.md (name=executor).
_REQUIRED_AGENT_NAMES = frozenset({"planner", "executor", "reducer"})


def bootstrap_real_agent_runtime() -> None:
    """Populate sandbox provider, runtime stores, and agent registry.

    Idempotent via a module-level sentinel. Safe to call from any entrypoint
    that drives :func:`workflow.start_task_center_run` with
    ``runner=None`` (real LLM path).
    """
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return

    from sandbox.provider.bootstrap import bootstrap_sandbox_provider

    bootstrap_sandbox_provider()

    from runtime.app_factory import ensure_runtime_stores_ready

    ensure_runtime_stores_ready()

    assert _PROFILE_ROOT.is_dir(), f"Agent profile root missing: {_PROFILE_ROOT}"

    from agents.definition.loader import load_agents_tree
    from agents.definition.registry import list_definitions, register_definition

    registered = {d.name for d in list_definitions()}
    if not _REQUIRED_AGENT_NAMES.issubset(registered):
        loaded = list(load_agents_tree(_PROFILE_ROOT))
        assert loaded, f"load_agents_tree({_PROFILE_ROOT}) returned no definitions"
        for defn in loaded:
            register_definition(defn)
        registered = {d.name for d in list_definitions()}

    missing = _REQUIRED_AGENT_NAMES - registered
    assert not missing, f"Agent registry missing required definitions: {sorted(missing)}"

    _BOOTSTRAPPED = True


__all__ = ["bootstrap_real_agent_runtime"]
