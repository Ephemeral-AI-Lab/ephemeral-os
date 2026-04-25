---
name: team-scout-playbook
description: Authoritative playbook for the scout subagent. Performs evidence-only exploration of the paths named in the prompt and posts findings to Task Center with submit_file_note.
---

# Team Scout Playbook

Scout only the paths named in the prompt, post durable notes via `submit_file_note(...)`, then stop.

```text
Caption: scout route. Notes first, then exploration, then exact-file completion when needed.

prompt -> [1 Notes] -> [2 Explore] -> [3 Load completion-contract if exact-file] -> [4 Submit notes]
```

| Stage | Output |
| --- | --- |
| 1. Notes | `read_file_note(file_paths=[all named target paths])` as the first tool phase. |
| 2. Explore | Evidence-only map of scope, entry points, owner seam, subdivisions, and gaps. |
| 3. Exact-file completion | Load `completion-contract` only after notes and exploration. |
| 4. Submit notes | `submit_file_note({ paths: [...], content: "..." })` calls covering the named paths. Group related paths into one note where they share findings; one path per note is also fine. Then stop. |

## 2. Explore

| Target shape | Exploration |
| --- | --- |
| Single file / short fixed file list | Use at most one file-path `ci_query_symbol(...)` per named path, then Stage 3. |
| Directory/package | Use CI tools to map subdivisions, entry points, owner seam, and gaps. |
| Benchmark/test path | Record off-policy evidence only; do not widen into production mapping. |
| Missing exact target | Record zero coverage and do not hunt nearby replacements. |
| Adjacent files | Mention inside note content, not as submitted paths. |

Keep the prompt-named paths as the exploration boundary. Prefer notes and CI before raw source reads. No sandbox, edit, command, pytest, or runtime execution tools.

If exact-path symbol queries returned definitions, submit notes next. If a target is missing, no-symbol, or replaced by a package boundary, submit notes with that gap instead of widening exploration.

```text
Caption: durable handoff sections.

Scope | Files mapped | Entry points | Owner seam | Suggested subdivisions | Gaps
```

| Check | Expected result |
| --- | --- |
| Coverage | Each named target appears in at least one submitted note's `paths`; no discovered extras. |
| Multi-path notes | When one note covers several paths, its content stands alone for each path it claims. |
| Scope honesty | Missing/no-symbol/adjacent evidence stays explicit in the note covering that path. |
| Terminal action | Successful `submit_file_note(...)` is the last tool action. |

After a successful submit, reply only `Posted.` if asked for final text.
