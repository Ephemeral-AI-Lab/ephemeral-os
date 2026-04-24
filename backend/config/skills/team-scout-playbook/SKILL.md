---
name: team-scout-playbook
description: Authoritative playbook for the scout subagent. Performs evidence-only exploration of assigned target paths and posts findings to Task Center with submit_file_notes.
---

# Team Scout Playbook

Scout only assigned `target_paths`, post durable notes, then finish with exactly one `submit_file_notes(...)`.

```text
Caption: scout route. Notes first; completion reference loads only for exact-file completion.

payload -> [1 Notes] -> [2 Explore] -> [3 Exact-file completion?] -> [4 Submit notes]
```

| Stage | Output |
| --- | --- |
| 1. Notes | `read_file_note(file_paths=[all assigned target_paths])` as the first tool phase. |
| 2. Explore | Evidence-only map of scope, entry points, owner seam, subdivisions, and gaps. |
| 3. Exact-file completion | Load `completion-contract` only for a single file or short fixed file list. |
| 4. Submit notes | One `submit_file_notes({ prompt, scoped_paths })` using the assigned path keys. |

The first tool phase contains only the required `read_file_note(...)` call. Do not batch CI, source reads, diagnostics, or structure queries with it.

## 2. Explore

| Target shape | Exploration |
| --- | --- |
| Single file / short fixed file list | Use at most one file-path `ci_query_symbol(...)` per assigned path, then Stage 3. |
| Directory/package | Use CI tools to map subdivisions, entry points, owner seam, and gaps. |
| Benchmark test path without test ownership | Inspect only enough snippet to understand expected behavior, then map production-owner evidence or gaps. |
| Missing exact target | Record zero coverage and do not hunt nearby replacements. |
| Context-only adjacent files | Record as hypotheses unless assigned. |

Keep `target_paths` as the exploration boundary. Prefer notes and CI before raw source reads. Do not use sandbox, edit, or runtime execution tools.

```text
load_skill_reference(
  skill_name="team-scout-playbook",
  reference_name="completion-contract"
)
```

If exact-path symbol queries returned definitions, submit notes next. If a target is missing, no-symbol, or replaced by a package boundary, submit notes with that gap instead of widening exploration.

```text
Caption: durable handoff sections.

Scope | Files mapped | Entry points | Owner seam | Suggested subdivisions | Gaps
```

| Check | Expected result |
| --- | --- |
| Coverage | Every assigned target appears once in `scoped_paths`; directory targets stay directory keys and fixed-file targets stay exact files. |
| Multi-path prompt | Path-labeled findings that stand alone when read back. |
| Scope honesty | Missing/no-symbol/off-policy/adjacent hypotheses stay explicit. |
| Terminal action | Exactly one `submit_file_notes(...)`; findings are not left only in prose. |

If a final response is required after the tool returns, reply only `Posted.`
