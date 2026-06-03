# OCC gate parity review (sandbox)

Area: OCC gate — commit gating; gitignore / outside-workspace direct merge;
git-tracked through the gate.

Source precedence: Python `/tmp/oldpy/backend/src/sandbox/occ/**` (ground truth)
> `docs/architecture/sandbox/occ.html` (corroboration) > invariant checklist.

## Scope note (READ FIRST)

The crate handed to me — `sandbox/crates/eos-occ/src/**` — is **only the
queue + routing-shape skeleton**. It defines the `CommitTransactionPort` and
`OccRouteProvider` *traits* and a stub `AllGatedRouteProvider`
(`service.rs:74-85`) but contains **no real validation, no real gitignore
oracle, and no real gated/direct staging**. The actual OCC gate behavior — the
thing every invariant is about — lives in the daemon's implementations of those
two traits:

- `sandbox/crates/eos-daemon/src/dispatcher.rs:1467` `impl CommitTransactionPort
  for LayerStackCommitTransaction` → `validate_prepared` / `validate_gated_group`
  / `validate_direct_group` (dispatcher.rs:2216-2322).
- `sandbox/crates/eos-daemon/src/dispatcher.rs:1730` `impl OccRouteProvider for
  LayerStackRouteProvider` → `is_ignored` (1731-1746) → `gitignore_matches`
  (2379-2417).

I audited both crates. Citing only `eos-occ` would have produced a false
"match" on the gate's core dynamics, because `eos-occ` itself never gates
anything.

## Ground truth

The Python OCC pipeline, in order:

1. **Route** (`changeset_preparation.py:99-134`). For each change:
   `normalize_layer_path` first (REJECT on `ValueError`); then `.git`/`.git/*`
   → `DROP`; then `_is_gitignored(path, snapshot)` → `DIRECT`; else `GATED`.
   Gitignore is evaluated against a **layer-stack snapshot**, not live disk
   (`SnapshotGitignoreOracle`, `gitignore.py:150-194`), via
   `PathspecGitignoreOracle` (`gitignore.py:36-147`) which honours **nested
   per-directory `.gitignore`**, the **directory-exclusion seal**, `!`
   re-includes, and `**`/full pathspec semantics (`pathspec.GitIgnoreSpec`).
2. **Group by `(route, path)`** and attach a **running base hash** per group:
   multiple changes to one path are chained, re-hashing the intermediate
   content after each `WriteChange`/`DeleteChange`
   (`changeset_preparation.py:81-163`).
3. **Serialize + batch** in the single `occ-commit-queue` thread
   (`commit_queue.py:59-152`). Disjoint, non-atomic, non-overlay-capture groups
   batch into ONE CAS; atomic or overlay-capture items are split out
   (`_disjoint_batches`, `commit_queue.py:224-252`). CAS conflicts retry up to
   `MAX_OCC_CAS_RETRIES = 3` (`commit_queue.py:27`), then every path becomes
   `ABORTED_VERSION` (`_cas_exhaustion_result`, 279-304).
4. **Transaction** (`commit_transaction.py:61-137`): read live active manifest
   under the publisher lock, validate each group, stage accepted deltas, call
   `publish_layer` once. DIRECT skips hash checks and supports symlinks; GATED
   checks the base-hash chain and rejects symlinks
   (`path_staging.py:91-109, 279-317`). GATED also applies `EditChange`
   search/replace **inside** the transaction and chains the hash across the
   group (`path_staging.py:261-277, 295-317`). Atomic-all-or-nothing and
   overlay-capture-gate-failure both convert ACCEPTED→DROPPED
   (`commit_transaction.py:313-327`).
5. **Maintenance**: auto-squash when manifest depth exceeds
   `AUTO_SQUASH_MAX_DEPTH = 100` (`service.py:34`, `maintenance.py`).
6. **Overlay → OCC change conversion** (`overlay_change_conversion.py:19-72`):
   write threads `content_path`+`final_hash`, delete→`DeleteChange`,
   symlink→`os.readlink`, opaque→`OpaqueDirChange`.

Doc corroboration: `docs/architecture/sandbox/occ.html:177-185` (route table:
DROP/DIRECT/GATED/REJECT), `:164` (`MAX_OCC_CAS_RETRIES`), `:166`
(direct skips hash, gated checks + rejects symlinks).

Key constants (ground truth): `MAX_OCC_CAS_RETRIES = 3`; `max_batch_size = 64`;
`batch_window_s = 0.002`; `AUTO_SQUASH_MAX_DEPTH = 100`; SHA-256 content hash;
CAS exhaustion uses `attempts >= max_cas_retries` (`commit_queue.py:180`).

## Rust mapping

| Python | Rust |
|---|---|
| `RouteDecision` / `FileStatus` enums | `Route` / `OccStatus` (`route.rs:17-76`) — wire strings match byte-for-byte |
| `service.apply_changeset` | `OccService::apply_changeset` (`service.rs:161-169`) |
| `changeset_preparation` route + `.git` drop | inlined in `prepare_changeset_with_base_hashes` (`service.rs:218-265`) |
| `_is_gitignored` / `PathspecGitignoreOracle` | `OccRouteProvider::is_ignored` → `LayerStackRouteProvider::is_ignored` + `gitignore_matches` (`dispatcher.rs:1731-1746, 2379-2417`) |
| `commit_queue` (thread, drain, batch, CAS) | `CommitQueue` / `CommitWorker` (`commit_queue.rs:118-471`) |
| `commit_transaction.revalidate_and_publish` | `LayerStackCommitTransaction::revalidate_and_publish` (`dispatcher.rs:1467-1543`) |
| `path_staging` GATED/DIRECT stagers | `validate_gated_group` / `validate_direct_group` (`dispatcher.rs:2262-2322`) |
| `content_hashing` SHA-256 | `hash_bytes` / `hash_current` (`dispatcher.rs:2355-2377`) |
| `overlay_change_conversion` | `overlay_change_conversion.rs:24-40` → `eos_overlay::…into_layer_change` (`path_change.rs:114-140`) |
| `AUTO_SQUASH_MAX_DEPTH = 100` | `service.rs:19`; gate at `dispatcher.rs:1633` |

## Invariant table

| # | Invariant | Status | Severity | Python file:line | Rust file:line | Note |
|---|---|---|---|---|---|---|
| 1 | OCC is the gate that decides what is committed | match | none | `commit_transaction.py:61-137` | `dispatcher.rs:1467-1543` | Single transaction validates then publishes once. |
| 2 | gitignored OR outside-workspace bypass the gate (DIRECT) | partial | high | `changeset_preparation.py:117-123`; `path_staging.py:91-99` | `service.rs:238-242`; `dispatcher.rs:2262-2268` | DIRECT for gitignored exists, but gitignore routing is wrong (see #6 / D1). "Outside-workspace" is NOT an OCC route on either side — see Extra finding E1. |
| 3 | git-tracked items merged THROUGH the gate (OCC/conflict) | match | none | `path_staging.py:295-317` | `dispatcher.rs:2270-2322` | GATED compares base-hash vs live manifest → `AbortedVersion` on mismatch. |
| 4 | Commit queue serializes; content hashing detects conflicts | match | none | `commit_queue.py:59-152`; `content_hashing.py:14-15` | `commit_queue.rs:319-341`; `dispatcher.rs:2362-2366` | One `occ-commit-queue` thread; SHA-256. Constants all match. |
| 5 | Overlay change → changeset conversion preserved | match | low | `overlay_change_conversion.py:19-72` | `overlay_change_conversion.rs:24-40`; `path_change.rs:114-140` | write/delete/symlink(readlink)/opaque preserved. Detail divergence D4 (eager byte read vs content_path threading). |
| 6 | Route classifies gitignored vs tracked vs outside, matching gitignore.py | divergent | high | `gitignore.py:36-194` | `dispatcher.rs:1731-1746, 2379-2417` | Rust uses a hand-rolled single-`.gitignore` matcher; no nested `.gitignore`, no `**`, `*` crosses `/`, no directory seal. See D1. |
| — | `MAX_OCC_CAS_RETRIES` literal | match | none | `commit_queue.py:27` (`= 3`) | `commit_queue.rs:51` (`= 3`) | Equal. |
| — | `max_batch_size` / `batch_window_s` | match | none | `commit_queue.py:66-67` (`64` / `0.002`) | `commit_queue.rs:40, 47` (`64` / `0.002`) | Equal. |
| — | `AUTO_SQUASH_MAX_DEPTH` literal | match | none | `service.py:34` (`= 100`) | `service.rs:19` (`= 100`) | Equal. |
| — | CAS exhaustion comparison operator | match | none | `commit_queue.py:180` (`attempts >= max`) | `commit_queue.rs:289` (`attempts >= max`) | Equal (`>=`). |
| — | atomic default `True` | match | none | `changeset.py:216` | `dispatcher.rs:1770` passes `true` | Daemon always commits atomic. |
| — | EditChange applied inside GATED stager | divergent | medium | `path_staging.py:261-277` | `dispatcher.rs:687-717` (resolved pre-OCC) | See D2 — equivalent for the 1-change/path daemon path. |
| — | REJECT route reachability in prepare | divergent | low | `changeset_preparation.py:105-108` | `service.rs:227-258` (never emits Reject) | See D3 — invalid paths fail upstream at `LayerPath::parse`. |
| — | multi-change-per-path running hash chain | partial | low | `changeset_preparation.py:151-163` | `service.rs:227-258` (1 decision per change) | See D5 — latent; not reachable via current daemon callers. |

## Disparities

### D1 (HIGH, divergent) — gitignore routing is a hand-rolled single-file matcher; nested `.gitignore` is invisible, so DIRECT vs GATED is misclassified

- Python: `LayerStackRouteProvider` ground truth is `SnapshotGitignoreOracle` +
  `PathspecGitignoreOracle` (`gitignore.py:36-194`). It reads a `.gitignore`
  **at every directory level** (`_read_gitignore(dir_rel)`, `gitignore.py:186-189`),
  evaluates with `pathspec.GitIgnoreSpec` (`gitignore.py:135`), applies the
  **directory-exclusion seal** (`gitignore.py:78-86`), `!` re-includes
  (`gitignore.py:122-125`), and full git pathspec including `**`.
- Rust: `LayerStackRouteProvider::is_ignored` reads **only the root**
  `.gitignore` — `stack.read_bytes(".gitignore")` (`dispatcher.rs:1734`) — and
  matches with the hand-rolled `gitignore_matches` / `gitignore_rule_matches` /
  `wildcard_match` (`dispatcher.rs:2379-2443`). It:
  - never reads `subdir/.gitignore`, so nested ignore rules don't exist;
  - has no `**` support (`gitignore_rule_matches` only special-cases `*`);
  - `wildcard_match` lets `*` cross `/` (git's `*` does not — `dispatcher.rs:2419-2443`);
  - has no directory-exclusion seal; `!` is a plain last-match toggle.
- **Behavioral consequence (this is the "silently miss a key dynamic" worry):**
  a path ignored only by a **nested** `.gitignore` (e.g. `frontend/node_modules/x`
  ignored by `frontend/.gitignore`) is routed **GATED instead of DIRECT** in
  Rust. It then gets base-hash-validated and can spuriously surface
  `AbortedVersion` on a build artifact that Python would last-writer-wins
  through DIRECT. Conversely a `**`-only or cross-dir pattern can mis-route the
  other way. This flips real conflict semantics on real paths.
- Same wrong oracle is used in the metrics path: `occ_route_metrics`
  (`dispatcher.rs:1795`) calls the same `gitignore_matches`.
- Suggested fix: replace the hand-rolled matcher with the `ignore`/`gitignore`
  crate (`ignore::gitignore::GitignoreBuilder`) and read per-directory
  `.gitignore` from the snapshot the same way `_read_gitignore(dir_rel)` does,
  walking each ancestor level. Add parity tests mirroring
  `backend/tests/live_e2e_test/sandbox/occ/test_direct_route.py` covering nested
  ignore, `**`, `!` re-include, and `*` not crossing `/`.

### D2 (MEDIUM, divergent) — EditChange search/replace is resolved BEFORE OCC in Rust, not inside the GATED stager

- Python: the GATED stager applies `EditChange` (`apply_search_replace`)
  **inside** `revalidate_and_publish`, against the content read from the live
  manifest, chaining the hash (`path_staging.py:261-277, 295-317`). The edit and
  the conflict check are one atomic step on live content.
- Rust: `op_edit_file` reads the base content, applies all edits, computes the
  pre-edit `base_hash`, and submits a finished `LayerChange::Write` plus
  `(path, base_hash)` to OCC (`dispatcher.rs:663-717`). OCC's
  `validate_gated_group` then only re-compares `base_hash` against the live
  manifest (`dispatcher.rs:2303-2315`).
- This is **behaviorally equivalent for the daemon's 1-change-per-path path**:
  the base-hash compare still rejects (`AbortedVersion`) if the file changed
  between the read and the publish, matching Python's gate outcome. There is a
  TOCTOU widening — the Rust edit reads content with `LayerStack::open` then
  drops it before OCC re-reads under the publisher path — but the final
  base-hash CAS still closes the window for the published layer. Note: Rust's
  edit-of-missing-file surfaces at the daemon op (`dispatcher.rs:674-685`,
  `aborted_version`) rather than via OCC's `missing_file_status`
  (`path_staging.py:262-272`); status family matches.
- Suggested fix: none required for correctness; document that edit-apply moved
  out of the OCC crate into the daemon op. If multi-edit/multi-change groups are
  ever routed through OCC directly, re-introduce the in-transaction chain.

### D3 (LOW, divergent) — `Route::Reject` is effectively dead in Rust prepare

- Python `_route_change` calls `normalize_layer_path` and returns
  `RouteDecision.REJECT` with the `ValueError` message
  (`changeset_preparation.py:105-108`); the path still appears in the result as
  a `REJECTED` `FileResult`.
- Rust `prepare_changeset_with_base_hashes` only emits Drop/Direct/Gated
  (`service.rs:227-258`) — it never produces `Route::Reject`, because the input
  is already a parsed `LayerPath` (`LayerPath::parse`, `cas.rs:57-81`, mirrors
  `normalize_layer_path`). An invalid path fails earlier at
  `LayerChange`/`LayerPath::parse` and surfaces as a `DaemonError`/`CasError`,
  not as a per-path `Rejected` status.
- Equivalent rejection still occurs, but on a **different channel** (hard error
  vs per-file `REJECTED` status). `cas_exhaustion_result` and `validate_prepared`
  still *handle* `Route::Reject` defensively (`commit_queue.rs:454`,
  `dispatcher.rs:2235`), so the enum is wired but unreachable in prepare.
- Suggested fix: none required; note the status-channel shape change for callers
  that branch on per-file `rejected`.

### D4 (LOW, divergent) — overlay write conversion reads bytes eagerly instead of threading content_path/final_hash

- Python `build_overlay_write_change` keeps bytes on disk
  (`content_path` + `precomputed_hash`) so the OCC stager streams
  kernel-to-kernel and reuses the precomputed hash
  (`changeset.py:258-289`, `overlay_change_conversion.py:38-45`).
- Rust `into_layer_change` does `std::fs::read(content_path)` and builds
  `LayerChange::Write { content }` (`path_change.rs:118-123`), dropping the
  precomputed `final_hash` (recomputed later in `publish_layer`).
- Correctness is unaffected (same bytes, same SHA-256); this is a perf /
  large-file-memory divergence only.

### D5 (LOW, partial; latent) — multiple changes to the same path are not grouped or hash-chained in Rust

- Python groups changes by `(route, path)` and chains a **running base hash**
  across them (re-hashing after each Write/Delete) so the second change in a
  group validates against the first's result
  (`changeset_preparation.py:81-163`).
- Rust `prepare_changeset_with_base_hashes` pushes **one `PublishDecision` per
  change** (`service.rs:227-258`); two changes to the same path become two path
  groups, each validated against the **same** live base, last-writer-wins, with
  no intermediate chain.
- **Reachability:** not reachable through current daemon callers.
  `op_write_file`/`op_edit_file` send exactly one change; overlay capture
  (`capture_upperdir_for_occ`, `dispatcher.rs:929`) walks the upperdir and
  yields one change per filesystem entry, so a path appears at most once per
  changeset. Therefore latent. If a future caller submits 2+ changes for one
  path, the chain semantics diverge and `disjoint_batches` (which dedups by
  path) could also mis-handle same-path duplicates within one changeset.
- Suggested fix: if same-path multi-change ever becomes reachable, group by
  path in `prepare_changeset_with_base_hashes` and chain the hash like Python;
  otherwise document the one-change-per-path precondition.

## Extra findings

- **E1 — "outside-workspace direct merge" is not an OCC route on either side
  (checklist conflation; reportable three-way disagreement).** Neither Python
  `_route_change` (`changeset_preparation.py:99-134`) nor Rust
  `prepare_changeset` (`service.rs:227-265`) has an "outside workspace" branch;
  both route only DROP/DIRECT/GATED/REJECT. Both normalizers **reject** absolute
  and `..` paths outright (`changes.py:31-40`; `cas.rs:64-74`), so a path
  outside the workspace cannot become a changeset at all. The checklist's
  invariant #2 ("changes OUTSIDE the workspace are DIRECTLY merged") and #6
  ("classifies … outside-workspace") describe a higher-layer dispatch bypass, if
  any — it is **not** the OCC DIRECT route, which is gitignored-only. The
  architecture doc agrees: DIRECT = gitignored only (`occ.html:179-180`). This
  is a checklist-vs-code disagreement, surfaced per instructions; the
  gitignored-DIRECT half is real and audited (#2 partial / D1).

- **E2 — `parent_absent_from_manifest` short-circuit is Rust-only but
  behaviorally equivalent.** `validate_gated_group` accepts a gated write with
  `base_hash == None` when the parent directory is absent from every manifest
  layer (`dispatcher.rs:2289-2302, 2330-2343`). Python has no explicit
  short-circuit but reaches the same outcome: a missing file hashes to `None`,
  and `_hash_mismatch` accepts when `current == expected == None`
  (`path_staging.py:189-198, 303-316`). Rust-only optimization, verified
  equivalent for the create-on-absent case.

- **E3 — `is_published` / `is_success` predicates match.** Rust
  `OccStatus::is_published` = {Accepted, Committed} and `is_success` =
  {Accepted, Committed, Dropped} (`route.rs:64-75`) match Python
  `is_published_status` / `is_success_status` (`changeset.py:154-163`).

- **E4 — `disjoint_batches` parity, with one simplification.** Rust
  `disjoint_batches` (`commit_queue.rs:363-397`) splits on `atomic` or path
  overlap and matches Python's `_disjoint_batches` (`commit_queue.py:224-252`)
  for those two predicates, but it **drops the `_contains_overlay_capture`
  predicate** Python uses to also split overlay-capture changesets out of
  batches (`commit_queue.py:237-242, 271-276`). In Rust, `eos-occ` has no
  `ChangeSource` on a `LayerChange`, so overlay-capture-ness is not visible at
  the queue. In practice the daemon submits overlay captures one changeset at a
  time and always `atomic=true` (`dispatcher.rs:1770`), so the `atomic` split
  already keeps captures un-batched — equivalent for current callers, but the
  source-based guard is gone. Medium-interest detail, low current impact.

- **E5 — `_atomic_or_overlay_dropped` second clause is absent in Rust.** Python
  drops accepted paths when an **overlay-capture** changeset has any GATED
  failure even when `atomic=False` (`commit_transaction.py:321-327`). Rust only
  implements the atomic clause (`dispatcher.rs:1485-1496`). Since the daemon
  always commits `atomic=true`, the atomic clause covers it; the overlay-capture
  fallback for `atomic=False` callers is missing. Low current impact (no
  `atomic=false` caller found), but a real dropped branch.

## Open questions

1. Is there any non-daemon entry point (test harness, future plugin path) that
   submits a non-atomic changeset or 2+ changes for one path to `OccService`? If
   yes, D5 and E5 move from latent to active bugs.
2. Is the gitignore divergence (D1) covered by any Rust parity test? I found
   none under `eos-daemon`/`eos-occ` exercising nested `.gitignore` or `**`.
   Confirm before relying on DIRECT routing for nested build-artifact dirs.
3. Python evaluates gitignore against the **snapshot** manifest
   (`SnapshotGitignoreOracle`); Rust reads `.gitignore` from `LayerStack::open`
   live state at route time (`dispatcher.rs:1732-1736`) — confirm whether the
   route snapshot and the publish snapshot can diverge under concurrent
   `.gitignore` edits (a second, smaller divergence inside D1).
