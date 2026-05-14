"""Agent-variant routing — predicates and resolver.

Selects which concrete agent definition (e.g. ``executor_success_handoff``
vs ``executor_success_failure``) to spawn from a base agent name plus the
caller's :class:`ContextScope`, based on registered predicates.
"""
