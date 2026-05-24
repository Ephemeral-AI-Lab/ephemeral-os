"""Per-tool-call workspace execution context.

Ephemeral workspace owns the daemon pipeline used by normal tool calls:
snapshot lease, namespace overlay, upperdir capture, OCC publish, plugin
dispatch, and background shell job compatibility.
"""

from sandbox.ephemeral_workspace.pipeline import EphemeralPipeline

__all__ = ["EphemeralPipeline"]
