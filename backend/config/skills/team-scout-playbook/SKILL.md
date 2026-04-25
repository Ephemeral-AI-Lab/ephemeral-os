---
name: team-scout-playbook
description: Authoritative playbook for the scout subagent. Performs evidence-only exploration of the paths named in the prompt and posts findings to Task Center with submit_file_note.
---

# Team Scout Playbook

Scout only the paths named in the prompt, post durable notes via `submit_file_note(...)`, then stop.

```text
Caption: scout route. Explore the named paths, then exact-file completion when needed, then submit.

prompt -> [1 Explore] -> [2 Load completion-contract if exact-file] -> [3 Submit notes]
```

| Stage | Output |
| --- | --- |
| 1. Explore | Evidence-only map of scope, entry points, owner seam, subdivisions, and gaps for the named paths. |
| 2. Exact-file completion | Load `completion-contract` only after exploration, when the prompt named a single file or short fixed file list. |
| 3. Submit notes | `submit_file_note({ paths: [...], content: "..." })` calls covering the named paths. Group related paths into one note where they share findings; one path per note is also fine. Then stop. |

## 1. Explore

| Target shape | Exploration |
| --- | --- |
| Single file / short fixed file list | Use at most one file-path `ci_query_symbol(...)` per named path, then Stage 3. Do not query test labels. |
| Directory/package | Use CI tools to map subdivisions, entry points, owner seam, and gaps. |
| Benchmark/test path | Record off-policy evidence only; do not query or read it, and do not widen into production mapping. |
| Missing exact target | Record zero coverage and do not hunt nearby replacements. |
| Adjacent files | Mention inside note content, not as submitted paths. |
| Prompt leakage | Ignore any test path, test id, F2P/P2P id, benchmark filename, or failing-test label when choosing CI queries. |

Keep the prompt-named paths as the exploration boundary. Prefer notes and CI before raw source reads. No sandbox, edit, command, pytest, or runtime execution tools.

If exact-path symbol queries returned definitions, submit notes next. If CI returns test references, discard them from the handoff; production definitions and production callers are the only routing evidence. If a target is missing, no-symbol, or replaced by a package boundary, submit notes with that gap instead of widening exploration.

```text
Caption: durable handoff sections.

Scope | Files mapped | Entry points | Owner seam | Suggested subdivisions | Gaps
```

| Check | Expected result |
| --- | --- |
| Coverage | Each named target appears in at least one submitted note's `paths`; no discovered extras. |
| Multi-path notes | When one note covers several paths, its content stands alone for each path it claims. |
| Scope honesty | Missing/no-symbol/adjacent evidence stays explicit in the note covering that path. |
| Production-only content | Do not cite test files, test labels, benchmark ids, or F2P/P2P ids as proof; if only test evidence exists, record an unresolved production gap. |
| Terminal action | Successful `submit_file_note(...)` is the last tool action. |

After a successful submit, reply only `Posted.` if asked for final text.
