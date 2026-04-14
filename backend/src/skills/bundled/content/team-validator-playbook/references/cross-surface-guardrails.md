# Cross-Surface Guardrails

Use this reference only when the touched change affects public serialization, schema shape, or docs-visible output.

## Rules

- Must add one nearby cross-surface guardrail in addition to the originally failing test.
- Must choose the smallest guardrail that exercises the same public surface and keep it in the same behavior family as the touched change.
- Never widen to broad repo-wide coverage just because the changed code is public.
- Never skip the guardrail when the changed code can affect schema wrappers, serializer output, or docs-visible examples.
- Must not stop at the original failing test alone.
- Must not widen to the full test suite.
