"""Runtime bootstrap surface.

The ``runtime`` package owns process-wide runtime configuration and the
module-level store singletons that non-server entrypoints (benchmarks,
live-e2e harness, CLI helpers) hydrate before driving the production agent
pipeline. Replaces the deleted ``server.app_factory`` runtime symbols.
"""
