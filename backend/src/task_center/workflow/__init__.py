"""Workflow package.

Workflow DTOs/enums live in :mod:`task_center._core.state`; lifecycle and
workflow-start sequencing live in their dedicated submodules (``lifecycle``,
``starter``). Callers import from the canonical submodule path; the package
root deliberately re-exports nothing.
"""
