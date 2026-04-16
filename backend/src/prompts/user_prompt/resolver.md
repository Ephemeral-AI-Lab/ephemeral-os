# Resolver User Prompt

Use this template for `resolver` tasks that repair a shared blocker root cause.

````text
## Your task

1. Please read the assigned resolver task and blocker context.
2. Analyze the shared root cause, affected paths, and paused sibling impact.
3. Explore only enough to confirm the root cause and the smallest repair surface.
4. Implement the smallest correct fix for the shared blocker.
5. Verify the fix against the acceptance criteria and apply a fix if the criteria are not met.
6. Submit the final resolver summary with `submit_task_summary(type="success", summary=...)` or report failure with evidence.

## Assigned resolver task

```markdown
{{task_spec}}
```

{{#if scope_paths}}
## Scope
{{scope_paths}}
{{/if}}

{{#if active_blockers}}
## Active blockers (in-progress)
{{active_blockers}}

{{/if}}
{{#if context_from_dependencies}}
## Context from dependencies
{{context_from_dependencies}}

{{/if}}
{{#if recent_scope_changes}}
## Recent changes in your scope
{{recent_scope_changes}}

{{/if}}
{{#if parent_context}}
## Parent context
{{parent_context}}
{{/if}}
````
