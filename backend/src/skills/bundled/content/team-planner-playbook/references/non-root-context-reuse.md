# Non-Root Context Reuse

Use this reference only on child planning turns or prompts with `## Scoped Expansion`.

## Workflow

1. Must reuse inherited briefings, artifacts, and known owner boundaries before fresh exploration.
2. Must spend at most one live confirmation step on the one unresolved owner when siblings are already mapped.
3. Must emit direct lanes for already-mapped siblings instead of replanning the whole repository.

## Rules

- Must keep exact file paths until a live artifact confirms an exact node id.
- Must recover real live filenames instead of guessed aliases.
- Never invent replacement nodes, replacement files, or broad substitute ownership from a stale test name.
