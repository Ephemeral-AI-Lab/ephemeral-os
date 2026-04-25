# Completion Contract

Use only in the exact-file completion stage when the prompt names a single file or a short fixed file list.

## Flow

```text
Caption: exact-file scout completion keeps the handed scope as the deliverable.

named paths
  -> one file-path ci_query_symbol per named path
  -> definitions found? submit notes
  -> missing/no-symbol/replaced by directory? submit notes with gap
```

Exact-file completion queries only the named production paths. Do not query test labels, test file paths, benchmark filenames, or F2P/P2P ids even if they appear in the prompt.

| Situation | Note outcome |
| --- | --- |
| Exact definitions exist | Record scope, entry points, owner seam, subdivisions, and gaps. |
| No-symbol exact file | Record why it should not be used as `scope_paths`; list live directory/nested-file evidence only as adjacent evidence. |
| Missing exact file | Record zero coverage for that path and no nearby replacement search. |
| Benchmark test target without test ownership | Record off-policy target and recommend production-owner scouting. |
| Prompt mentions adjacent files | Record as unresolved hypotheses unless explicitly named. |

For multiple paths, group findings by logical chunk and make each note's content stand alone for every path it claims. Suggested subdivisions are usually `[]` or `none` for single-file scouts.

## Submit Shape

Use `submit_file_note(paths=[...], content="...")` calls. Each named target path must appear in at least one submitted note's `paths`. Do not replace a directory target with discovered child files. Do not leave findings only in visible prose.

After a successful submit, reply only `Posted.` if asked for final text.
