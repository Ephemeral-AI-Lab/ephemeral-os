# Completion Contract

Use only in the exact-file completion stage when `target_paths` is a single file or a short fixed file list.

## Flow

```text
Caption: exact-file scout completion keeps the handed scope as the deliverable.

notes read
  -> one file-path ci_query_symbol per assigned path
  -> definitions found? submit notes
  -> missing/no-symbol/replaced by directory? submit notes with gap
```

| Situation | Note outcome |
| --- | --- |
| Exact definitions exist | Record scope, entry points, owner seam, subdivisions, and gaps. |
| No-symbol exact file | Record why it should not be used as `scope_paths`; list live directory/nested-file evidence only as adjacent evidence. |
| Missing exact file | Record zero coverage for that path and no nearby replacement search. |
| Benchmark test target without test ownership | Record off-policy target and recommend production-owner scouting. |
| Context mentions adjacent files | Record as unresolved hypotheses unless assigned. |

For multiple scoped paths, make the prompt path-labeled. Suggested subdivisions are usually `[]` or `none` for single-file scouts.

## Submit Shape

Use one non-empty `prompt` plus the exact assigned `scoped_paths`; do not replace a directory target with discovered child files. Do not use the old per-item note shape or leave findings only in visible prose.

If the tool returns and a final response is required, reply only `Posted.`
