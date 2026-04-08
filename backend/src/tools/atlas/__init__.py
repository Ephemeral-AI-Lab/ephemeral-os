"""Atlas toolkit — planner-side read access to the Project Atlas (Phase 2).

Exposes a single tool, :func:`atlas_lookup`, for planners to consult the
persistent scout brief cache. Writers (``atlas_builder`` / ``atlas_refresher``)
use the ``submit_atlas`` posthook instead; they never call this toolkit.
"""

from tools.atlas.lookup import atlas_lookup
from tools.core.base import BaseToolkit


class AtlasToolkit(BaseToolkit):
    """Read-only access to the persistent Project Atlas for planner agents."""

    def __init__(self) -> None:
        super().__init__(
            name="atlas",
            description="Persistent cross-run scout brief cache (Project Atlas).",
            tools=[atlas_lookup],
            instructions=(
                "Look up cached scout briefs for one or more subsystems. "
                "`atlas_lookup` returns a decision per subsystem:\n"
                "- `use`: a staged artifact ref is included — attach it to a worker briefing.\n"
                "- `refresh`: spawn an atlas_refresher WorkItem with this subsystem in "
                "`stale_subsystems`, then chain the worker via `deps`.\n"
                "- `scout`: no cached brief — spawn a plain `scout` WorkItem.\n"
                "Semantic questions ('how does X work', 'why does Y exist') must "
                "always go to a fresh scout, never the atlas."
            ),
        )


__all__ = ["AtlasToolkit", "atlas_lookup"]
