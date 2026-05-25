"""Per-tool-call workspace execution context.

Ephemeral workspace owns the daemon pipeline used by normal tool calls:
snapshot lease, namespace overlay, upperdir capture, OCC publish, plugin
dispatch, and coroutine-bound background tool execution.
"""

__all__: list[str] = []
