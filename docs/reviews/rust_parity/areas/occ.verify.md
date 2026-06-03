# OCC gate parity — INDEPENDENT VERIFICATION

Area: OCC gate — commit gating; gitignore / outside-workspace direct merge;
git-tracked through the gate (sandbox).

Method: re-derived from Python ground truth at
`/tmp/oldpy/backend/src/sandbox/occ/**` + `layer_stack/changes.py`, the Rust
`sandbox/crates/eos-occ/src/**` + `eos-daemon/src/dispatcher.rs` +
`eos-overlay/src/path_change.rs` + `eos-protocol/src/cas.rs`, corroborated by
`docs/architecture/sandbox/occ.html`. I opened every cited file. Line numbers
below are the ones I actually observed (the investigation's dispatcher anchors
drifted by ~20 lines; behavior is identical, anchors corrected here).

## Invariant verdict table

| # | Invariant | independent_status | severity | decisive bilateral anchor |
|---|---|---|---|---|
| 1 | OCC is the gate that decides what is committed | confirmed_match | none | PY `commit_transaction.py:61-137` (one tx: snapshot→validate each group→publish once) ↔ RS `dispatcher.rs:1455-1521` (`validate_prepared`→`publish_layer` once, single `revalidate_and_publish`). |
| 2 | gitignored OR outside-workspace bypass the gate (DIRECT) | investigator_overstated (adjusted) | high | DIRECT-for-gitignored real both sides: PY `changeset_preparation.py:117-123` ↔ RS `service.rs:231-235`. BUT routing oracle divergent (see #6/D1). "Outside-workspace" is NOT an OCC route on either side — see New finding N1 / agrees with investigation E1. |
| 3 | git-tracked items merged THROUGH the gate (OCC/conflict) | confirmed_match — *contingent on #6* | medium | GATED base-hash vs live manifest → `AbortedVersion`: PY `path_staging.py:295-317` + `_hash_mismatch` 295-317 ↔ RS `validate_gated_group` `dispatcher.rs:2248-2300` (line 2282: `hash_current(...).as_deref() == base_hash` else `AbortedVersion`). The *mechanism* matches exactly; its guarantee is undercut for tracked paths that #6 mis-routes to DIRECT (over-match case, N2 below). |
| 4 | Commit queue serializes; content hashing detects conflicts | confirmed_match | none | One `occ-commit-queue` thread: PY `commit_queue.py:88-93` ↔ RS `commit_queue.rs:35,201-206`. SHA-256: PY `content_hashing.py:14` ↔ RS `dispatcher.rs:2340-2344`. Constants all equal (below). |
| 5 | Overlay change → changeset conversion preserved | confirmed_match | low | write/delete/symlink(readlink)/opaque: PY `overlay_change_conversion.py:19-72` ↔ RS `path_change.rs:109-134`. Detail divergence D4 (eager byte read; correctness unaffected). |
| 6 | Route classifies gitignored vs tracked, matching gitignore.py | confirmed_disparity | high | PY `pathspec.GitIgnoreSpec` + nested per-dir `.gitignore` + dir-seal + `!` (`gitignore.py:36-194`) ↔ RS hand-rolled root-only matcher `dispatcher.rs:1709-1724` (`read_bytes(".gitignore")` root only) + `gitignore_matches`/`gitignore_rule_matches`/`wildcard_match` `2357-2421`. See D1 + N2 (dir-only any-level miss — investigator_missed sub-finding). |
| — | `MAX_OCC_CAS_RETRIES` literal | confirmed_match | none | PY `commit_queue.py:27` (`=3`) ↔ RS `commit_queue.rs:47` (`=3`). |
| — | `max_batch_size` / `batch_window_s` | confirmed_match | none | PY `commit_queue.py:66-67` (`64`/`0.002`) ↔ RS `commit_queue.rs:38,44` (`64`/`0.002`). |
| — | `AUTO_SQUASH_MAX_DEPTH` literal | confirmed_match | none | PY `service.py:34` (`=100`) ↔ RS `service.rs:18` (`=100`); gate `dispatcher.rs:1611` (`<= AUTO_SQUASH_MAX_DEPTH`). |
| — | CAS exhaustion comparison operator | confirmed_match | none | PY `commit_queue.py:180` (`attempts >= self._max_cas_retries`) ↔ RS `commit_queue.rs:278` (`attempts >= max_cas_retries`). Both `>=`. |
| — | atomic default `True` / daemon always atomic | confirmed_match | none | PY default `True` (`changeset.py` CommitOptions) ↔ RS `apply_occ_changeset` passes `true` (`dispatcher.rs:1749`). |
| — | EditChange applied inside GATED stager | confirmed_disparity (adjusted) | medium | PY in-transaction `path_staging.py:261-277` ↔ RS resolved pre-OCC in `op_edit_file` `dispatcher.rs:644-697`. Equivalent for 1-change/path daemon path (D2). |
| — | `Route::Reject` reachability in prepare | confirmed_disparity (adjusted) | low | PY emits `REJECT` FileResult `changeset_preparation.py:105-108` ↔ RS `prepare_changeset_with_base_hashes` `service.rs:220-251` never emits Reject; invalid path fails at `LayerPath::parse` `cas.rs:53-77` (CasError, not per-file status). D3. |
| — | multi-change-per-path running hash chain | confirmed_disparity (adjusted, latent) | low | PY groups+chains `changeset_preparation.py:81-163` ↔ RS one PublishDecision per change `service.rs:220-251`. Not reachable via current daemon callers. D5. |

## Disparity adjudication

**D1 (gitignore hand-rolled, root-only) — CONFIRMED, HIGH.** Re-derived
independently. RS `is_ignored` (`dispatcher.rs:1709-1724`) reads only
`stack.read_bytes(".gitignore")` at the root (line 1713); never reads
`subdir/.gitignore`. `gitignore_rule_matches` (2378-2395) special-cases only
`*` (no `**`), and `wildcard_match` (2397-2421) lets `*` greedily cross `/`.
Python uses `pathspec.GitIgnoreSpec.from_lines` per directory level
(`gitignore.py:135`), reads `.gitignore` at every ancestor dir
(`gitignore.py:186-189`), and applies the directory-exclusion seal
(`gitignore.py:78-86`). The same wrong oracle drives metrics
(`occ_route_metrics`, `dispatcher.rs:1773`). Only two gitignore tests exist
(`dispatcher.rs:3683` root `target/`+`*.pyc`, `3696` route metrics) — neither
covers nested `.gitignore`, `**`, or `*`-cross-slash. Investigation Open
Question #2 ("no Rust parity test for nested/`**`") **confirmed**.

**D1 is INCOMPLETE — investigator_missed sub-finding (see N2).** The
investigation lists nested-`.gitignore`, `**`, `*`-cross-slash, and the seal,
but omits the single most common real trigger: **dir-only (trailing-slash)
patterns with no internal slash are root-anchored in Rust but match at any
level in git.** Verdict on D1 itself stays CONFIRMED; N2 strengthens it.

**D2 (EditChange resolved pre-OCC) — CONFIRMED, MEDIUM, behaviorally
equivalent for daemon.** `op_edit_file` (`dispatcher.rs:644-697`) reads base
bytes, computes `base_hash` from PRE-edit content (line 645), applies all
edits to a local `String` (667-683), then submits a finished
`LayerChange::Write` + `(path, base_hash)` to `apply_occ_changeset` (689-697).
OCC's `validate_gated_group` (2282) only re-compares that `base_hash` against
live. Equivalent gate outcome for one-change-per-path. Confirmed the noted
status-channel difference: edit-of-missing-file surfaces as `aborted_version`
at the daemon op (`dispatcher.rs:654-665`), NOT via OCC `missing_file_status`
(`path_staging.py:262-272`). Status family matches.

**D3 (`Route::Reject` dead in prepare) — CONFIRMED, LOW.** RS
`prepare_changeset_with_base_hashes` (`service.rs:220-251`) emits only
Drop/Direct/Gated. Invalid paths are unrepresentable: `LayerPath::parse`
(`cas.rs:53-77`) rejects absolute (60), `..` (68), empty (73), NUL (56) —
mirrors Python `normalize_layer_path` (`changes.py:33,39`). The enum is still
handled defensively in `cas_exhaustion_result` (`commit_queue.rs:442`) and
`validate_prepared` (`dispatcher.rs:2213`). Rejection happens on a different
channel (CasError vs per-file `REJECTED`). Adjusted: real, low impact.

**D4 (overlay write reads bytes eagerly) — CONFIRMED, LOW.**
`OverlayPathChange::into_layer_change` (`path_change.rs:116`) does
`std::fs::read(content_path)` into `LayerChange::Write { content }`, dropping
the captured `final_hash` (`path_change.rs:47`; recomputed in publish). Python
threads `content_path`+`precomputed_hash` (`overlay_change_conversion.py:38-45`,
`commit_transaction.py:225-258`). Same bytes / same SHA-256; perf-only.

**D5 (no same-path grouping/chain) — CONFIRMED, LOW, latent.** RS pushes one
`PublishDecision` per change (`service.rs:220-251`); no `(route,path)` grouping
or running-hash chain. Python chains (`changeset_preparation.py:151-163`). Not
reachable: `op_write_file`/`op_edit_file` send exactly one change;
upperdir capture (`path_change.rs:148-157`) yields one change per fs entry.
Latent. `disjoint_batches` (`commit_queue.rs:351-385`) dedups across changesets
by path but not within one changeset's `path_groups` — consistent with the
one-change-per-path precondition.

**Investigator's E2–E5 spot-checked:**
- E2 (`parent_absent_from_manifest` short-circuit, RS-only) — CONFIRMED
  equivalent. RS `validate_gated_group` 2267-2280 accepts gated write with
  `base_hash==None` when parent absent from every layer
  (`parent_absent_from_manifest` 2308-2321); Python reaches same outcome via
  `_hash_mismatch` accepting `current==expected==None`
  (`path_staging.py:303-316`).
- E5 (`_atomic_or_overlay_dropped` 2nd clause absent in RS) — CONFIRMED. Python
  `commit_transaction.py:321-327` also drops accepted paths when an
  overlay-capture changeset has any gated failure with `atomic=False`; RS
  `atomic_validation_drop_result` (`dispatcher.rs:1533-1563`) implements only
  the atomic clause. Daemon always `atomic=true` (1749), so covered today; the
  `atomic=False` overlay branch is genuinely missing. Low current impact.

## New findings

**N1 — agrees with investigator E1 (kept for completeness, not a new
disparity).** "Outside-workspace direct merge" is NOT an OCC route on either
side. PY `_route_change` (`changeset_preparation.py:99-134`) and RS
`prepare_changeset_with_base_hashes` (`service.rs:218-251`) both emit only
DROP/DIRECT/GATED(/REJECT). Both normalizers reject absolute/`..` outright
(`changes.py:33,39` ↔ `cas.rs:60,68`), so an out-of-workspace path cannot
become a changeset. Doc agrees: DIRECT = gitignored only
(`occ.html` §4.3 — "Used for gitignored paths"). The checklist phrase
"outside-workspace direct merge" describes a higher-layer dispatch bypass, not
this OCC DIRECT route. Not a code-vs-code disparity.

**N2 — investigator_missed (sub-finding of D1): dir-only any-level gitignore
patterns are root-anchored in Rust.** This is the most common real trigger and
is absent from the investigation's D1 enumeration. Re-derived by hand against
`gitignore_rule_matches` (`dispatcher.rs:2378-2395`):

- Pattern `node_modules/`, path `frontend/node_modules/x`:
  `dir_only=true`, pattern→`node_modules`; line 2385-2386 returns
  `path=="node_modules" || path.starts_with("node_modules/")` → **false** →
  routed **GATED**. Git/pathspec: a no-internal-slash pattern matches at ANY
  level → DIRECT. **Under-match.** The canonical gitignore entries the doc
  itself cites — `node_modules/`, `target/`, `build/`, `__pycache__/` — all use
  trailing slashes and all break under nesting (monorepos / per-package
  artifact dirs). Consequence: a build artifact under a nested ignored dir is
  base-hash-validated and can spuriously surface `AbortedVersion` that Python
  would last-writer-wins through DIRECT.
- Note the asymmetry the same trace exposes: pattern `node_modules` WITHOUT a
  trailing slash falls through to `path.split('/').any(|p| p==pattern)`
  (line 2394) → matches any level → **correct**. So the bug is specifically the
  trailing-slash (dir-only) form, which is the form humans actually write.

**N3 — #3 "match" is contingent on #6 (false-match-adjacent crack;
over-match direction is data-loss class).** The GATED gate mechanism (#3) is
byte-for-byte correct, but its protection only applies to paths #6 routes to
GATED. The over-match direction of D1 flips a git-**tracked** path to DIRECT,
skipping the base-hash gate entirely:

- Pattern `logs/*.log`, path `logs/sub/x.log`: pattern has `/` and `*`, so line
  2388-2389 calls `wildcard_match("logs/*.log","logs/sub/x.log")`; the `*`
  greedily crosses `/` and matches `sub/x` → **true** → routed **DIRECT**.
  Git: `*` never crosses `/`, so `logs/*.log` does NOT match `logs/sub/x.log` →
  **GATED**. In Rust the tracked file is published last-writer-wins, silently
  clobbering a concurrent edit that Python would reject with `AbortedVersion`.
  This directly undercuts invariant #3's guarantee. Lower frequency than N2 but
  data-loss class, so #3 is recorded as "confirmed_match — contingent on #6".

**N4 — `combine_prepared` debug_assert vs Python AssertionError (cosmetic).**
PY `_combine_prepared` raises `AssertionError` if >1 atomic batched
(`commit_queue.py:257-258`); RS uses `debug_assert!` (`commit_queue.rs:392`),
compiled out in release. Both unreachable because `disjoint_batches` never
batches atomic items. Cosmetic, no behavioral divergence. Not a disparity.

## Overall verdict

The investigation's directional verdicts all hold; I overturned nothing.
Adjudication: D1 CONFIRMED (HIGH) and is the real parity gap — independently
reproduced, no Rust parity test covers it. D2/D3/D5/E5 CONFIRMED as
adjusted/latent. The gate mechanism (#1/#3/#4) and overlay conversion (#5) and
all constants (CAS retries=3, batch=64, window=0.002, squash=100, `>=`,
SHA-256) are genuine byte-for-byte matches — no false match found among them.

Two enrichments beyond the investigation:
- **N2 (investigator_missed sub-finding of D1):** dir-only any-level patterns
  (`node_modules/`, `target/`, `build/`) are root-anchored in Rust — the most
  common real misroute, omitted from D1's list. Does not flip D1 (already
  HIGH), strengthens it.
- **N3:** invariant #3's "match" is contingent on #6's divergent routing; the
  over-match direction (`*` crossing `/`) routes a tracked file to DIRECT and
  silently clobbers — a data-loss-class crack adjacent to a "match" row. #3
  recorded as confirmed_match-contingent, severity raised to medium.

No FALSE MATCH that would let a broken dynamic pass as "match" survives: the
one match row at risk (#3) is the gate mechanism itself, which is correct; its
contingency on #6 is captured in N3 and #6 is already a confirmed disparity.
