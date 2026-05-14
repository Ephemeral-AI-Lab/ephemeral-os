"""Context-engine exceptions.

Living in a leaf module (no project-internal imports) so every other engine
file can pull error names from one place without forming an import cycle.
"""

from __future__ import annotations


class ContextEngineError(Exception):
    """Generic context engine failure."""


class RecipeScopeError(ContextEngineError):
    """A recipe was called with a :class:`ContextScope` missing required fields."""


class MissingContextRecipeError(ContextEngineError):
    """An agent definition was selected for composition but has no
    ``context_recipe`` declared in frontmatter."""


class AgentDefinitionValidationError(ContextEngineError):
    """A registered :class:`AgentDefinition` references unknown or invalid
    variants / predicates / context recipes — caught at startup."""
