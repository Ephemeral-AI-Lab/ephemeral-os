"""Neutral home for cross-package tool pre/post hooks.

Hooks here are imported by both ``tools.submission`` (terminals) and
``tools.isolated_workspace`` (enter/exit) without creating a layering cycle
between those packages. Each hook lazily imports ``sandbox.api`` inside its
``run`` method to avoid the circular import documented in
``sandbox.host.isolated_workspace_lifecycle``.
"""
