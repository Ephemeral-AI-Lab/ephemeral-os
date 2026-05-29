# Plan: `replace_all` flag + `multi_edit` tool for the sandbox edit stack

Status: IMPLEMENTED 2026-05-29 (both changesets landed; see §12 Progress)
Owner: planner handoff
Scope: tool surface + sandbox edit plumbing + both OCC and tool_primitives apply sites
Mode: SHORT consensus (no pre-mortem / expanded e2e required)

---

## 1. PRD

### 1.1 Problem
`edit_file` enforces "the anchor must occur exactly once, else abort". Agents that need to
replace every occurrence of a string must call `edit_file` once per occurrence or rewrite the
whole file with `write_file`. There is also no way to apply a batch of related edits to one
file atomically from the tool layer.

### 1.2 Goals
1. Add a `replace_all: bool` flag to the existing `edit_file` tool (Claude-Code style). Default
   `false` keeps today's "anchor must be unique, else abort" behavior exactly. When `true`,
   replace every occurrence in the current committed content; the only failure is `count == 0`
   ("anchor not found").
2. Add a separate `multi_edit` tool: single `file_path` + an ordered `edits[]` array applied
   sequentially against evolving content, all-or-nothing. Each edit carries its own
   `replace_all`. Mirrors Claude Code's FileEditTool + MultiEdit split.
3. Keep it lean: reuse the existing list-of-edits plumbing and atomic group staging; add one
   small shared pure helper rather than duplicating the count/replace logic across the two
   independent apply sites.

### 1.3 Non-goals
- No `expected_occurrences` on the public tool surface. It is not exposed AND is not threaded
  through the payload at all — the payload comprehension at `api/tool/edit.py` L31–34 drops it,
  `SearchReplaceEdit` has no field for it, and both daemon readers default it to `1`
  independently (`_normalize_args` at `tool_primitives/edit.py` L45 and `_edit_changes` at
  `dispatch.py` L399). (CC does not expose an occurrence count and we should not either.)
- No `base_hash` plumbing for `replace_all` edits (see §6 — last-write-wins is accepted and
  documented).
- No CC-style cosmetic "old/new overlap" guard. The OCC re-count at commit time and the
  per-site count check already cover correctness.
- `applied_edits` is NOT changed to report true occurrence counts. It stays
  `len(edits)` (see §5, decision D4).
- No new daemon op, no provider changes, no architecture-doc restructure (a one-line note to
  `docs/architecture/tools` is optional follow-up, not in scope).

### 1.4 Users / callers
Agents calling `edit_file` and (new) `multi_edit` through the sandbox tool layer, across all
three execution contexts: OCC (ephemeral with workspace binding), overlay (ephemeral, no
binding), and isolated workspace. The latter two share the `tool_primitives` apply path.

### 1.5 Success criteria
- `edit_file(..., replace_all=true)` replaces all occurrences in one call; with `count == 0`
  it aborts with "anchor not found".
- `edit_file` default behavior (`replace_all` omitted/false) is byte-for-byte unchanged,
  including the `count != expected → aborted` path.
- `multi_edit` applies N edits sequentially-against-evolving-content, all-or-nothing, with
  per-edit `replace_all`, and requires zero daemon/OCC changes beyond what Changeset 1 adds.
- `replace_all` semantics are identical across OCC and tool_primitives sites because both call
  one shared helper.
- Adding `replace_all` to an edit changes its `changeset_id`; two edits differing only by
  `replace_all` do not collide.

---

## 2. Architecture facts this plan is built on (verified against code)

### 2.1 Request plumbing — the spine
The `DAEMON_OP_EDIT_FILE` payload built in `backend/src/sandbox/api/tool/edit.py` (L29–36) is
the single chokepoint. BOTH apply sites read from it:
- OCC path: `_edit_changes` (`backend/src/sandbox/daemon/workspace_tool/dispatch.py` L390–413)
  reads payload edit dicts → builds `EditChange`.
- tool_primitives path: `_normalize_args` (`backend/src/sandbox/shared/tool_primitives/edit.py`
  L33–55) reads the same edit dicts.

The payload comprehension currently emits only `{"old_text", "new_text"}` per edit and DROPS
everything else. If `replace_all` is not added there, it reaches neither apply site and the
feature is inert. **This is the change everything else hangs on.**

### 2.2 Two independent apply sites
- **Site A — OCC** (ephemeral-with-binding): `_apply_edit_content`
  (`backend/src/sandbox/occ/path_staging.py` L45–72). Decodes bytes, `count = text.count(old)`,
  aborts on `count == 0` and on `count != expected_occurrences`, else
  `text.replace(old, new, expected_occurrences)`. Returns `bytes | FileResult` — failure is a
  `FileResult(status=ABORTED_OVERLAP, ...)`, NOT an exception.
- **Site B — tool_primitives** (overlay + isolated): `edit_file` / `_normalize_args`
  (`backend/src/sandbox/shared/tool_primitives/edit.py` L15–55). Same count/replace logic but
  **raises `ValueError`** on failure. Routed via `VERB_TABLE["edit_file"]` in
  `backend/src/sandbox/shared/tool_primitives/__init__.py`; reached through
  `backend/src/sandbox/ephemeral_workspace/pipeline.py` and
  `backend/src/sandbox/overlay/namespace_entrypoint.py`.

The two sites have NO shared helper today and surface failure differently (FileResult vs
raise). That difference is the real design work — see §4.

### 2.3 OCC merge / determinism
- `revalidate_and_publish` (`backend/src/sandbox/occ/commit_transaction.py` L61–137) re-reads
  the CURRENT active manifest inside the commit lock and re-stages every group against current
  content. Edits apply against current committed content, not a stale snapshot.
- `EditChange` (`backend/src/sandbox/occ/changeset.py` L88–106) has NO `base_hash` and is
  excluded from `_requires_base_hash` (`backend/src/sandbox/occ/changeset_preparation.py`
  L166–171). The occurrence-count check is the edit's ONLY concurrency/staleness signal.
- `compute_changeset_id` / `_change_signature` (`changeset.py` L302–366) feed replay-stability
  and dedup. `_change_signature` for `EditChange` currently encodes `old_text`, `new_text`,
  `expected_occurrences` (L327–330).
- `_stage_group` (`path_staging.py` L175–218) applies a path group's changes sequentially
  against evolving `_StagedPathState` and aborts the whole group on first failure (returns
  `delta=None` → nothing staged). tool_primitives `edit_file` loops similarly. **Multi-edit
  atomicity already exists at both sites** — `multi_edit` is purely a tool-layer feature.

### 2.4 Import direction (shared-helper viability)
`backend/src/sandbox/occ/path_staging.py` already imports from `sandbox.shared`
(`sandbox.shared.clock`, `sandbox.shared.timing_keys`). `sandbox.shared.models` is consumed by
both occ and tool_primitives. So a new pure helper in `sandbox/shared/` is importable by both
`occ/path_staging.py` and `shared/tool_primitives/edit.py` with NO new layering violation.
`sandbox.shared.models` docstring forbids importing provider/runtime/OCC/overlay internals —
the helper must stay pure (str in, str out, raise on error), so it satisfies that rule.

### 2.5 Changeset-id test reality
`test_prepared_changeset_id_is_stable_across_replay`
(`backend/tests/unit_test/test_sandbox/test_occ/test_occ_emitters.py` L143+) asserts only
equality-across-replay and inequality-for-distinct-inputs. No literal 16-hex id is pinned in
any test (`grep "changeset_id =="` is empty; the `"cs_..."` strings are hand-injected
fixtures, not computed). So both conditional and unconditional `_change_signature` additions
pass the suite; we pick conditional-add to avoid perturbing any persisted in-flight ids
(decision D5).

---

## 3. Decisions (locked + derived)

| # | Decision |
|---|----------|
| D1 | `replace_all` is a bool flag on `edit_file`, default `false`. (locked by user) |
| D2 | `multi_edit` is a separate tool; one `file_path`, ordered `edits[]`, all-or-nothing; per-edit `replace_all`. (locked by user) |
| D3 | One shared pure helper `apply_search_replace(...)` used by both apply sites. (lean) |
| D4 | `applied_edits` stays `len(edits)`, NOT occurrence count. Reporting true counts would need threading counts back through the daemon payload from both sites — out of scope. |
| D5 | `_change_signature` adds `replace_all` CONDITIONALLY (`if change.replace_all`), so only edits that use the feature get a perturbed id. |
| D6 | `count == 0` aborts even under `replace_all` ("anchor not found"). `replace_all` skips only the `count != expected` check, never the `count == 0` check. |
| D7 | `replace_all` OCC concurrency = accepted last-write-wins; documented, no base_hash. |
| D8 | Land Changeset 1 (replace_all end-to-end) first; Changeset 2 (multi_edit) builds on it and inherits per-edit replace_all for free. |

---

## 4. Shared helper design (the lean core)

New file: `backend/src/sandbox/shared/edit_apply.py`

```python
"""Pure search/replace primitive shared by OCC and tool_primitives apply sites."""

from __future__ import annotations


class SearchReplaceError(ValueError):
    """Raised when a search/replace edit cannot be applied as requested."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def apply_search_replace(
    text: str,
    old: str,
    new: str,
    *,
    replace_all: bool,
    expected_occurrences: int = 1,
) -> str:
    """Apply one search/replace to already-decoded text. Pure; raises on failure.

    - old must be non-empty.
    - count == 0 always aborts ("anchor not found"), including under replace_all.
    - replace_all=False: count must equal expected_occurrences, else abort.
    - replace_all=True: replace every occurrence (skip the !=expected check).
    """
    if not old:
        raise SearchReplaceError("edit anchor old_text must be non-empty")
    count = text.count(old)
    if count == 0:
        raise SearchReplaceError("anchor not found")
    if replace_all:
        return text.replace(old, new)
    if count != expected_occurrences:
        raise SearchReplaceError("anchor occurrence count mismatch")
    return text.replace(old, new, expected_occurrences)
```

**Adapter split (the load-bearing detail).** The helper always RAISES `SearchReplaceError`.
The two call sites adapt differently:
- Site A (OCC `_apply_edit_content`): keep the existing UTF-8 decode and the
  `UnicodeDecodeError → FileResult` branch at the call site; wrap the helper call in
  `try/except SearchReplaceError` and convert to `FileResult(status=ABORTED_OVERLAP,
  message=exc.message)`. The "anchor not found" and "anchor occurrence count mismatch"
  messages are preserved verbatim so existing message-asserting tests keep passing.
- Site B (tool_primitives `edit_file`): decode stays at the call site; let
  `SearchReplaceError` propagate (it is a `ValueError` subclass, matching today's raised
  `ValueError`). The pre-loop `if not old:` raise is replaced by the helper's check.

The helper takes already-decoded `str` so decode/UnicodeDecodeError policy stays per-site and
the helper has no I/O. This is the whole "both sites collapse to one call" claim, made precise.

If, contrary to §2.4, a reviewer finds the import edge unacceptable, fall back to leaving the
~3-line branch duplicated at each site (each adds one `if replace_all:` branch). The shared
helper is preferred; the fallback is named only so the executor is not blocked.

---

## 5. File-by-file change list

### Changeset 1 — `replace_all` end-to-end (land first)

Ordered so the payload spine is threaded before the apply sites consume it.

1. **`backend/src/sandbox/shared/edit_apply.py`** (NEW)
   Add `apply_search_replace` + `SearchReplaceError` exactly as §4. Pure, no imports beyond
   stdlib.

2. **`backend/src/sandbox/shared/models.py`** (`SearchReplaceEdit`, L181–186)
   Add field `replace_all: bool = False`. Keep `kw_only=True` frozen dataclass shape.
   (No change to `EditFileRequest`/`EditFileResult`.)

3. **`backend/src/sandbox/api/tool/edit.py`** (payload comprehension, L31–34) — THE SPINE
   Add `"replace_all": edit.replace_all` to the per-edit dict so it reaches the daemon payload.

4. **`backend/src/sandbox/occ/changeset.py`**
   - `EditChange` (L88–106): add field `replace_all: bool = False`; in `__post_init__` coerce
     `object.__setattr__(self, "replace_all", bool(self.replace_all))`.
   - `_change_signature` (L327–330): conditionally add
     `if change.replace_all: sig["replace_all"] = True` (decision D5). Do NOT add it
     unconditionally.

5. **`backend/src/sandbox/occ/path_staging.py`** (`_apply_edit_content`, L45–72) — Site A
   Keep the decode + `UnicodeDecodeError → FileResult` branch. Replace the count/abort/replace
   block (L58–72) with: call `apply_search_replace(text, change.old_text, change.new_text,
   replace_all=change.replace_all, expected_occurrences=change.expected_occurrences)` inside
   `try/except SearchReplaceError as exc:` → return `FileResult(path=path,
   status=FileStatus.ABORTED_OVERLAP, message=exc.message)`; on success return
   `result_text.encode("utf-8")`.
   Note: the shared helper's empty-`old` message has no `for {path}` suffix that
   `dispatch.py` L404 currently emits; verified NO test asserts that string, so this is
   cosmetic — do not re-add the suffix or chase a phantom test.

6. **`backend/src/sandbox/daemon/workspace_tool/dispatch.py`** (`_edit_changes`, L390–413)
   Read `replace_all` from each raw edit dict
   (`bool(raw.get("replace_all", False))`) and pass it to `EditChange(...)`.

7. **`backend/src/sandbox/shared/tool_primitives/edit.py`** — Site B
   - `_normalize_args` (L33–55): include `replace_all` in the per-edit tuple
     (`bool(raw.get("replace_all", False))`); widen the tuple type to
     `tuple[str, str, int, bool]`.
   - `edit_file` (L15–30): for each edit call
     `current = apply_search_replace(current, old, new, replace_all=replace_all,
     expected_occurrences=expected)`; drop the now-redundant inline `if not old` /
     count-mismatch raises (the helper raises `SearchReplaceError`, a `ValueError`).
   - Note: the shared helper's empty-`old` message has no `for {path}` suffix that
     `tool_primitives/edit.py` L21 currently emits; verified NO test asserts that string, so
     this is cosmetic — do not re-add the suffix or chase a phantom test.

8. **`backend/src/tools/sandbox/edit_file/edit_file.py`**
   - `EditFileInput` (L25–40): add
     `replace_all: bool = Field(default=False, description="Replace every occurrence of `old_text` instead of requiring a unique match.")`.
   - `_normalize_edits` (L61–69): accept `replace_all` and set it on the `SearchReplaceEdit`.
   - `edit_file` signature + call (L80–111): thread `replace_all` from the input into
     `_normalize_edits`.

9. **`backend/src/tools/sandbox/edit_file/edit_file.py`** (`EditFileOutput.applied_edits`,
   L55–58) AND **`backend/src/tools/sandbox/edit_file/prompt.py`** (`get_edit_file_description`)

   a. `edit_file.py` — update the `EditFileOutput.applied_edits` Pydantic field description
      (currently `"Number of replacements applied."`). It goes stale under `replace_all`: per
      D4, `applied_edits` is `len(edits)`, NOT occurrence count. Reword to e.g. `"Number of
      edits applied (not occurrence count; one replace_all edit counts as 1)."`

   b. `prompt.py` — update the three statements that go stale under `replace_all`:
   - L45 "`old_text` must be unique in the file" → qualify with "unless `replace_all=true`".
   - L56 "`applied_edits`: 1 on success" → note `applied_edits` counts edits, not occurrences
     (still 1 for one edit even when replace_all hits several spots).
   - L65–67 "find-and-replace across many occurrences — split into multiple calls" → point at
     `replace_all=true` (and at `multi_edit` once Changeset 2 lands).
   Add a one-line `replace_all` capability bullet and an example. Also add an explicit
   `replace_all` CONCURRENCY caveat to the prompt text: "`replace_all` replaces however many
   occurrences exist in the CURRENT committed content and does NOT detect concurrent edits to
   that file; prefer the default unique-match mode when correctness depends on the file being
   unchanged."

Net new LOC estimate: ~25 (helper) + ~15 (threading across 8 sites) ≈ ~40 LOC.

### Changeset 2 — `multi_edit` tool (tool-layer only; zero daemon/OCC changes)

Because `_edit_changes` already loops a list and `_stage_group` / tool_primitives already do
sequential-against-evolving + all-or-nothing, `multi_edit` adds NO daemon, OCC, or
tool_primitives change. It builds N `SearchReplaceEdit`s → one `EditFileRequest` →
`sandbox_api.edit_file`, reusing the exact path Changeset 1 wired.

10. **`backend/src/tools/sandbox/multi_edit/__init__.py`** (NEW)
    Mirror `edit_file/__init__.py`: re-export the impl module via `sys.modules[__name__]`.

11. **`backend/src/tools/sandbox/multi_edit/multi_edit.py`** (NEW)
    - `MultiEditInput`: `file_path: str`; `edits: list[MultiEditOp]` where each op is
      `{old_text: str, new_text: str = "", replace_all: bool = False}`; `description: str = ""`.
      `extra="forbid"`.
    - Build `tuple(SearchReplaceEdit(old_text=..., new_text=..., replace_all=...) for op in
      edits)` → one `EditFileRequest(path, edits=..., caller=..., description=...)` →
      `sandbox_api.edit_file`. Reject empty `edits` list with a typed error.
    - Reuse `mutation_tool_result`, `resolve_tool_sandbox_path`,
      `sandbox_id_or_missing_error_result`, and the same context helpers as `edit_file`.
    - Output model mirrors `EditFileOutput`; `applied_edits = len(edits)` (D4). Give the
      `applied_edits` field a description consistent with D4 (e.g. "Number of edits applied (not
      occurrence count)"), matching the wording chosen in step 9a.

12. **`backend/src/tools/sandbox/multi_edit/prompt.py`** (NEW)
    Document: single file, ordered edits applied sequentially against evolving content (edit N
    sees edit N-1's result), all-or-nothing (any failed edit aborts the whole call, nothing
    lands), per-edit `replace_all`, must have read the file first, and when to prefer
    `edit_file` (single change) vs `write_file` (whole rewrite). Include the SAME
    `replace_all` concurrency caveat as step 9b: "`replace_all` replaces however many
    occurrences exist in the CURRENT committed content and does NOT detect concurrent edits to
    that file; prefer the default unique-match mode when correctness depends on the file being
    unchanged."

13. **`backend/src/tools/_names.py`**
    Add `MULTI_EDIT_TOOL_NAME = "multi_edit"` and include it in `__all__`.

14. **`backend/src/tools/sandbox/_lib/registry.py`**
    `from tools.sandbox.multi_edit import multi_edit`; add `multi_edit` to the `tools` list in
    `make_sandbox_tools`.

Net new LOC estimate: ~90 (mostly the new tool + prompt), no plumbing churn.

---

## 6. Consideration #1 — OCC merge / `replace_all` concurrency semantics

`EditChange` has no `base_hash`; `revalidate_and_publish` re-stages against the CURRENT
committed manifest inside the commit lock. So:
- `replace_all=false` (today + unchanged): the `count != expected` check is the sole staleness
  signal. If a concurrent commit changed occurrence density, the count mismatches and the edit
  aborts — that is the existing optimistic-concurrency guarantee.
- `replace_all=true`: skipping the `!= expected` check means "replace however many occurrences
  exist in the current committed content." This is well-defined last-write-wins. It LOSES
  detection of a concurrent edit that changed occurrence density. **This is accepted and must
  be documented** (decision D7): `replace_all` is inherently "however many there are," so an
  occurrence-density mismatch is not a meaningful conflict for it. The `count == 0` check is
  retained (D6) so a `replace_all` against content where the anchor vanished still aborts with
  "anchor not found" rather than silently no-op'ing.
- We deliberately do NOT add `base_hash` plumbing for `replace_all` edits. That would mean
  promoting `EditChange` into `_requires_base_hash` and threading a hash from the tool layer —
  heavy, and it changes the semantics of plain edits too. Out of scope.

**Mandatory `_change_signature` change.** `compute_changeset_id` derives replay-stability and
dedup ids from `_change_signature`. Two edits identical except for `replace_all` MUST get
distinct ids or they collide in replay/dedup. Add `replace_all` to the `EditChange` branch of
`_change_signature` (conditionally, D5). This is not optional.

`multi_edit` concurrency: relies entirely on the existing atomic group staging (`_stage_group`
returns `delta=None` on first failure → whole group dropped; `CommitOptions.atomic` defaults
`True`). No new concurrency surface.

---

## 7. Consideration #2 — isolated vs ephemeral vs OCC: all three paths

All three contexts read the SAME `DAEMON_OP_EDIT_FILE` payload:
- OCC (ephemeral + binding): payload → `_edit_changes` → `EditChange` → `_apply_edit_content`
  (Site A).
- Overlay (ephemeral, no binding) AND isolated: payload → `tool_primitives` `_normalize_args`
  → `edit_file` (Site B). Isolated routing in
  `backend/src/sandbox/daemon/workspace_tool/dispatch.py` (`_active_isolated_pipeline_for`)
  funnels to the same `tool_primitives` verb table.

Therefore `replace_all` must be threaded through:
1. the API payload (step 3) — once, for all three contexts; and
2. BOTH apply-site readers — `_edit_changes` (step 6) and `_normalize_args` (step 7).

Both apply sites then call the SAME `apply_search_replace` helper (steps 5 and 7), so semantics
cannot diverge between OCC and tool_primitives. This closes the "isolated and ephemeral must
agree" requirement structurally rather than by parallel hand-maintenance.

Verification hook in the test plan (§9): a parity test asserts Site A and Site B produce
identical results for the same `(text, old, new, replace_all)` inputs.

---

## 8. Consideration #3 — lean

- The shared helper's primary justification is single source of truth / no divergence across
  the two apply sites (enforced by the §9 parity test), NOT leanness — it is roughly
  LOC-neutral versus a duplicated branch. LOC numbers below are informational only.
- Total net new LOC ≈ 40 (Changeset 1) + ~90 (Changeset 2 new tool/prompt); the plumbing churn
  itself is small.
- `expected_occurrences` is not exposed and is not threaded through the payload (both daemon
  readers default it to 1 independently); NOT added to the public tool surface.
- No overlap guard, no base_hash plumbing, no new daemon op.
- Changeset 2 touches zero daemon/OCC/tool_primitives code — strongest lean evidence and the
  reason to land Changeset 1 first.

---

## 9. Test plan

All under `backend/tests/unit_test/`. Run with `.venv/bin/pytest` (never global pytest).

### Helper (new)
`test_sandbox/test_edit_apply.py`
- `replace_all=False`, unique match → replaced once.
- `replace_all=False`, `count == 0` → raises `SearchReplaceError("anchor not found")`.
- `replace_all=False`, `count != expected` → raises `SearchReplaceError("anchor occurrence count mismatch")`.
- `replace_all=True`, 3 occurrences → all 3 replaced; `applied` semantics not asserted here.
- `replace_all=True`, `count == 0` → still raises "anchor not found" (D6).
- empty `old` → raises.

### OCC apply site (Site A)
`test_sandbox/test_occ/` — extend or add `test_edit_replace_all.py`
- `_apply_edit_content` with `replace_all=True` over multiple occurrences → all replaced,
  returns bytes.
- `replace_all=True`, anchor absent → `FileResult(status=ABORTED_OVERLAP, message="anchor not
  found")`.
- `replace_all=False` default path unchanged: occurrence mismatch → ABORTED_OVERLAP with the
  existing message.
- Multi-edit atomic abort: a path group whose 2nd `EditChange` fails leaves nothing staged
  (`delta is None`), first edit not published.

### Determinism (Site A id)
`test_sandbox/test_occ/test_occ_emitters.py` (add a case next to
`test_prepared_changeset_id_is_stable_across_replay`)
- Two `EditChange`s identical except `replace_all` → `compute_changeset_id` differs.
- `replace_all=False` edit id is unchanged vs the pre-feature signature (conditional-add
  guarantee, D5) — assert by constructing the same change and comparing to a `replace_all=True`
  variant's inequality plus a stable-across-replay check.

### tool_primitives (Site B) + parity
`test_sandbox/test_tool_primitives_edit_replace_all.py` (new)
- `edit_file` primitive with `replace_all=True` replaces all; with `count==0` raises
  `ValueError`/`SearchReplaceError`.
- Parity: for the same `(text, old, new, replace_all)`, Site A `_apply_edit_content` and Site B
  `edit_file` yield identical resulting content.
- Multi-edit sequential parity: across an evolving 2-edit sequence (edit 2 anchors on edit 1's
  output), Site A and Site B produce identical final content — locks no-divergence under the
  multi-edit evolving-content path, not just single edits.

### multi_edit tool (Changeset 2)
`test_tools/test_sandbox_toolkit/test_multi_edit.py` (new, alongside `test_edit_file.py`)
- Sequential apply: edit 2's `old_text` matches text produced by edit 1.
- All-or-nothing: a failing 2nd edit aborts the whole call; result reflects no change.
- Per-edit `replace_all`: one op with `replace_all=True`, another with default false, in one
  call.
- `applied_edits == len(edits)` (D4).
- Empty `edits` list → typed error.

### edit_file tool surface (Changeset 1)
`test_tools/test_sandbox_toolkit/test_edit_file.py` (extend)
- `replace_all=True` passes through to the request and produces an "edited" result.
- Default call (no `replace_all`) behavior unchanged.
- `applied_edits == 1` when a single `replace_all=True` edit hits 3 occurrences — locks D4
  behaviorally (edit count, not occurrence count).

---

## 10. RALPLAN-DR summary

### Principles
1. One semantic source of truth: `replace_all`/count behavior defined once, consumed by every
   apply site.
2. Thread through the existing spine (`DAEMON_OP_EDIT_FILE` payload); add no new transport.
3. Lean: reuse list-of-edits plumbing + atomic group staging already present in both sites.
4. Preserve existing default behavior byte-for-byte (default `replace_all=False`).
5. Make replay/dedup correctness explicit (`_change_signature` must see `replace_all`).

### Decision drivers (top 3)
1. Semantics must NOT diverge between OCC and tool_primitives (two independent apply sites).
2. Minimal blast radius / minimal net LOC.
3. Replay-stability + dedup correctness of `changeset_id`.

### Options

**Option A (CHOSEN): one shared pure helper, raising; per-site adapters.**
- Pros: single source of truth / no divergence across the two apply sites, enforced by the §9
  parity test (driver 1) — this is the lead rationale; import edge already exists (no layering
  violation, §2.4). (LOC-neutral vs Option B; not a lean win.)
- Cons: requires specifying the FileResult-vs-raise adapter split precisely (done in §4).

**Option B: duplicated ~3-line `if replace_all:` branch at each site.**
- Pros: zero new module, zero import-edge question.
- Cons: two implementations of the same semantics — exactly the divergence risk driver 1
  forbids; future edits must touch two places. Reserved only as fallback if the import edge is
  rejected.

Why A wins: it directly satisfies the strongest driver (no divergence, enforced by the §9
parity test) — the decisive factor, not LOC. The import edge is already present in the
codebase, so B's only advantage is moot.

**multi_edit shape — Option A (CHOSEN): single `file_path` + `edits[]`, tool-layer only.**
- Pros: matches CC's MultiEdit; zero daemon/OCC change (atomic group staging already exists);
  inherits per-edit `replace_all` from Changeset 1 for free.
- Cons: cannot span multiple files in one call (intentional; matches locked decision D2).

**multi_edit shape — Option B: a multi-file batch tool.**
- Pros: one call could edit several files.
- Cons: would require new request/result models and cross-path atomic semantics across path
  groups — large scope, contradicts lean and the locked single-`file_path` decision. Rejected.

Both option sets retain ≥2 viable options with the chosen one justified; no single-option
invalidation needed.

---

## 11. ADR

**Title:** `replace_all` flag + `multi_edit` tool on the sandbox edit stack

**Decision.** Add `replace_all: bool` (default false) to `edit_file` and a separate
`multi_edit` tool. Thread `replace_all` through the existing `DAEMON_OP_EDIT_FILE` payload and
into both apply sites. Centralize count/replace semantics in one pure helper
`sandbox.shared.edit_apply.apply_search_replace` that raises `SearchReplaceError`; OCC adapts
the raise to a `FileResult`, tool_primitives lets it propagate. Add `replace_all` to
`EditChange` and conditionally to `_change_signature`. `multi_edit` is tool-layer-only and
reuses existing atomic group staging.

**Drivers.** (1) No semantic divergence across the two independent apply sites. (2) Minimal
blast radius / lean. (3) Replay-stability + dedup correctness of `changeset_id`.

**Alternatives considered.** (a) Duplicated per-site branches — rejected: reintroduces the
divergence risk driver 1 forbids. (b) `base_hash` plumbing for `replace_all` to detect
concurrent density changes — rejected: heavy, changes plain-edit semantics, and `replace_all`
is inherently "however many there are." (c) Multi-file `multi_edit` — rejected: large new model
surface, contradicts the locked single-`file_path` shape. (d) Exposing `expected_occurrences`
on the public surface — rejected: CC doesn't, and it muddies the tool contract.

**Why chosen.** Option A is the only option that gives a single source of truth with no
divergence across the two independent apply sites — the decisive driver, enforced by the §9
parity test (it is LOC-neutral vs duplicated branches, so this is not a leanness argument). The
shared-helper import edge already exists, so its only competitor's advantage is moot.
`multi_edit` tool-layer-only is provably lean (zero daemon/OCC change) and inherits
`replace_all` from Changeset 1.

**Consequences.**
- Positive: one place defines edit semantics; default behavior unchanged; small diff; both
  contexts provably agree (parity test).
- Negative / accepted: `replace_all` is last-write-wins under OCC (loses occurrence-density
  conflict detection); documented (D7). `count == 0` still aborts (D6). `applied_edits` reports
  edit count, not occurrence count (D4).
- `changeset_id` for `replace_all=true` edits differs from a `false` counterpart by design;
  `false` edits keep their existing id (conditional-add, D5).

**Follow-ups (out of scope, track in open-questions).**
- Optional one-line note in `docs/architecture/tools` describing the shared helper and the
  `replace_all` last-write-wins semantic.
- Consider whether `multi_edit` should later report per-edit occurrence counts (would need
  daemon payload round-trip).

---

## 12. Progress (implementation report) — 2026-05-29

### Status: COMPLETE. Both changesets landed; all targeted suites green.

**Changeset 1 — `replace_all` (all steps done):**
1. NEW `backend/src/sandbox/shared/edit_apply.py` — `apply_search_replace` + `SearchReplaceError`.
2. `shared/models.py` `SearchReplaceEdit.replace_all: bool = False`.
3. `api/tool/edit.py` payload now emits `replace_all` per edit (the spine).
4. `occ/changeset.py` `EditChange.replace_all` (+ `__post_init__` coercion) and conditional
   `_change_signature` add (D5).
5. `occ/path_staging.py` `_apply_edit_content` calls the helper; keeps the UTF-8/decode
   branch; adapts `SearchReplaceError → FileResult(ABORTED_OVERLAP)`.
6. `daemon/workspace_tool/dispatch.py` `_edit_changes` reads `replace_all`.
7. `shared/tool_primitives/edit.py` `edit_file`/`_normalize_args` call the helper; inline
   `if not old` / count-mismatch raises removed (dead code this change created).
8–9. `tools/sandbox/edit_file/{edit_file,prompt}.py` — `replace_all` input field, threading,
   `applied_edits` D4 reword, prompt updates (unique-match qualifier, `replace_all`
   capability bullet + example + concurrency caveat).

**Changeset 2 — `multi_edit` (tool-layer only, zero daemon/OCC change):**
10–12. NEW `tools/sandbox/multi_edit/{__init__,multi_edit,prompt}.py`.
13. `tools/_names.py` `MULTI_EDIT_TOOL_NAME`.
14. `tools/sandbox/_lib/registry.py` registers `multi_edit` (roster now 9 tools).

**Tests:** `test_edit_apply.py` (9), `test_occ/test_edit_replace_all.py` (Site A + atomic
abort), `test_occ_emitters.py` (replace_all changeset-id case), `test_tool_primitives_edit_replace_all.py`
(Site B + Site A/B parity, single + multi-edit evolving), `test_tools/.../test_multi_edit.py` (6),
`test_edit_file.py` (+3). `test_daemon/test_file_dispatch.py` (`_edit_changes` OCC payload reader threads `replace_all` —
the load-bearing Site-A "both sites agree" link). Updated `test_api/test_edit.py` (payload now
carries `replace_all`), `test_edit_handler.py` (converged count-mismatch message),
`test_sandbox_toolkit/test_toolkit.py` (roster count 8→9, expected set, api_inputs). Net: 450+
passed across the tool layer + edit-related sandbox suites; `ruff check` clean on all changed
files; `mypy` clean on all new/changed lines (one pre-existing `_hash_mismatch` error in
untouched code left out of scope).

### Review / refactor pass (goal item 2)
- Removed the inline `if not old` / count-mismatch raises that the shared helper
  made dead in both apply sites (`tool_primitives/edit.py`, `path_staging.py`).
- Consolidated the duplicated tool-result projection: `write_file`, `edit_file`,
  and `multi_edit` repeated a ~28-line success/failure `mutation_tool_result`
  block (failure half byte-identical). Extracted
  `tools/sandbox/_lib/mutation_result.py::project_file_mutation` and routed all
  three tools through it — single source for the projection; `multi_edit` no
  longer adds a third copy. `write_file`/`edit_file` behavior is unchanged
  (verified by `test_sandbox_toolkit`, 78 passed).
- `ruff` clean; `mypy` clean on all new/changed lines. Pre-existing, out-of-scope
  type quirks left untouched and noted: `path_staging._hash_mismatch` (`str|None`
  assignment) and `mutation_result.normalize_timing_map` arg variance — both
  confirmed present at the clean baseline.
- Not removed (legacy the plan deliberately kept, noted not deleted):
  `expected_occurrences` plumbing (`_normalize_args`, `_edit_changes`, validation,
  BL-05 tests) is dead in production — the payload drops it and both readers
  default to 1 — but PLAN §1.3 explicitly keeps it out of scope; the
  `"old_text_not_found"` entry in `audit/conflict_markers.py` has no producer in
  the current edit path but is a cross-module contract list, unrelated to this work.

### Deviation from §4 (intentional — see open-questions.md)
The shipped helper does NOT raise on `count == 0` unconditionally in the non-`replace_all`
branch. §4's literal sketch contradicts §1.5 ("byte-for-byte unchanged default") and the two
BL-05 tests in `test_edit_handler.py` (`expected_occurrences==0` + absent anchor → no-op
success). The shipped helper preserves that, stays byte-for-byte identical to Site A for all
production inputs (`expected` is always 1), and still honors D6 for `replace_all`. One
message-only test assertion converged. Full rationale in `docs/plans/open-questions.md`.

### Deferred (not done; tracked in open-questions.md)
- `docs/architecture/tools` one-line note on the shared helper + last-write-wins semantic.
- `multi_edit` per-edit occurrence-count reporting (needs daemon payload round-trip).

### Pre-existing failures encountered (NOT caused by this work; confirmed via clean-baseline stash)
- `test_sandbox/test_daemon/test_daemon.py::test_services_cached_per_layer_stack_root`
- `test_sandbox/test_daemon/test_sandbox_overlay.py::test_operation_overlay_uses_shared_snapshot_layers_and_private_upperdir`
  (run_dir filesystem-cleanup assertion). Both fail identically with all edit changes stashed.

### Parallel-agent breakage repaired in passing (clear + compatible)
A concurrent rename moved `task_center/task_guidance/builders.py` →
`context_engine/task_guidance.py` and deleted the old package but left two dangling importers
(`task_center/agent_launch/task_guidance_dispatch.py`, `tools/subagent/run_subagent/run_subagent.py`);
both updated to the new path so `test_tools` collects. A separate `task_center.task_state` →
`task_center._core.task_state` rename was self-resolved by its owning agent during this session.
