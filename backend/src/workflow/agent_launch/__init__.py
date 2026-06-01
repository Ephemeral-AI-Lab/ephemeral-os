"""Per-agent launch-message composition.

Owns the four-row launch wire shape (system + ``<context>`` +
``<Task Guidance>`` + skill) and the builder dispatch by exact agent name.
``context_engine/`` is now context-only; this package wraps the rendered
context in its envelope and threads the role-specific prose through
``task_guidance``. Callers import from the canonical submodule path
(``composer``, ``entry_messages``, ``context_engine.skill_message``,
``context_engine.task_guidance``); the package root re-exports nothing.
"""
