# Cross-Surface Guardrails

Use this reference only when the touched change affects public serialization, schema shape, or docs-visible output.

## Rules

- Must add one nearby cross-surface guardrail in addition to the originally failing test.
- Must choose the smallest guardrail that exercises the same public surface.
- Must keep the added guardrail in the same behavior family as the touched change.
- Never widen to broad repo-wide coverage just because the changed code is public.
- Never skip the guardrail when the changed code can affect schema wrappers, serializer output, or docs-visible examples.

## One-shot example

If the changed file alters schema output in `pkg/root_model.py` and the originally failing test is `tests/test_root_model.py`, add one nearby schema guardrail such as `tests/test_json_schema.py`.

Must not stop at the original failing test alone.
Must not widen to the full test suite.
