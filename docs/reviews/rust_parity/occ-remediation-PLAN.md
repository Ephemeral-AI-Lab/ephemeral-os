# OCC gitignore routing — Rust parity remediation plan (PLAN ONLY)

> Phase 2 (correctness / data-safety), area **occ**, finding **D1 (HIGH)**.
> Report rows: `REPORT.md` Phase 2 occ line; `areas/occ.md` D1 + N2/N3; `areas/occ.verify.md` N2/N3.
> Ground truth: Python `/tmp/oldpy/backend/src/sandbox/occ/gitignore.py` (`SnapshotGitignoreOracle` + `PathspecGitignoreOracle`, `pathspec.GitIgnoreSpec`).
> Anchors are current `main`; the daemon file is being edited concurrently, so line numbers are approximate (`~`) — function names are authoritative.

## 0. The issue in one paragraph

OCC's route decision (`DIRECT` vs `GATED`) is driven by a gitignore oracle. `gitignored → DIRECT` (bypass the base-hash conflict gate, last-writer-wins); `tracked → GATED` (base-hash validated). Python evaluates this with a full `pathspec.GitIgnoreSpec` engine reading **per-directory** `.gitignore` from the layer-stack snapshot, with the directory-exclusion seal, `!` re-includes, `**`, and `*`-not-crossing-`/`. The Rust port (`sandbox/crates/eos-daemon/src/dispatcher.rs`) replaced it with a **hand-rolled, root-only matcher** (`is_ignored → gitignore_matches → gitignore_rule_matches → wildcard_match`) that reads only the root `.gitignore` and gets the per-pattern semantics wrong in two data-relevant ways. A second copy of the same wrong matcher backs the telemetry path (`occ_route_metrics`). The fix replaces the matcher with a correct, snapshot-fed, per-directory oracle and routes both consumers through it.

## 1. Root cause: a root-only, flat, hand-rolled matcher in place of a per-directory git oracle

`LayerStackRouteProvider::is_ignored` (`dispatcher.rs ~1700`) reads **only** `read_bytes(".gitignore")` at the stack root and matches with three hand-rolled fns. Confirmed divergences (severity-bearing first):

- **N2 (HIGH, most common misroute).** A dir-only pattern (`node_modules/`) hits the `dir_only` branch (`gitignore_rule_matches ~2385`) which is **root-anchored**: `path == "node_modules" || path.starts_with("node_modules/")` → misses `frontend/node_modules/x`. Git matches a no-slash dir pattern at **any depth**. The asymmetry: bare `node_modules` (no slash) goes to `split('/').any(==)` and matches at any depth, so the **conventional trailing-slash form is the one that breaks** — and it breaks with a single *root* `.gitignore`, before nesting matters. → spurious `AbortedVersion` on regenerated build artifacts.
- **N3 (HIGH, data-loss).** `wildcard_match` (`~2397`) lets `*` cross `/`, so `logs/*.log` matches `logs/sub/x.log` → routed **DIRECT** → the OCC conflict gate is skipped → a concurrent edit to a tracked file is **silently clobbered**. Git's `*` never crosses `/`.
- **Secondary (same root cause):** nested `.gitignore` invisible (root-only read); no `**`; no directory-exclusion seal (`!` is a plain per-path toggle); `occ_route_metrics` (`~1747`) does a **second independent** gitignore read+match, so telemetry inherits every divergence.

Consumer: the *only* `is_ignored` caller is `OccService::prepare_changeset_with_base_hashes` (`eos-occ/src/service.rs:231`): `ignored → Route::Direct` else `Route::Gated`.

## 2. Design decision: simplify to one shared free fn + the `ignore` crate (no new file/struct/field)

A 3-proposer + 1-grounded-API panel converged. The decisive technical fact (verified against docs.rs + the `ignore` crate source): **`GitignoreBuilder::add_line`'s `from:` argument is provenance/diagnostics only — anchoring comes from `GitignoreBuilder::new(root)`.** A single flat builder anchors every pattern at one root and silently mis-matches subdir rules (`/build`, `src/*.rs`). Correct nested gitignore therefore requires **one matcher per directory level**, walked deeper-wins, **and the directory seal is caller-owned** (the crate's `Gitignore::matched*` is parent-unaware; the seal lives in `WalkBuilder`, which we cannot use because we feed a snapshot, not a disk tree).

| Option | New file | New type | New field | Matcher engine | Verdict |
|---|---|---|---|---|---|
| **Baseline** (prior writeup) | `gitignore_oracle.rs` | `GitignoreOracle` struct | — | `ignore` crate | over-built |
| **Simplified + `ignore`** ✅ | none | none | none | `ignore` crate (per-pattern) + our per-level walk + seal | **recommended** |
| Zero-dep hand-roll | none | tiny local `Match` enum | none | re-derive N3/`**`/seal/`!`/dir-only by hand | smaller deps, **re-imports the exact bug class D1 fixes** |

**Recommendation: Simplified + `ignore`.** The risk-bearing code is the *per-pattern* matcher (N3 `*`-not-crossing-`/` via `literal_separator(true)`, `**`, dir-only-any-depth via the implicit `**/` prefix, `!` ordering, char classes) — that is precisely what the hand-roll got wrong and is data-loss-class. The `ignore` crate (ripgrep's engine, battle-tested) owns that; we own only the cheap composition (~40–60 lines). A zero-dep hand-roll minimizes the dependency tree but **maximizes the risk we are paid to remove**, and is *more* matcher code, not less.

**Decision (resolved):** use the **`ignore` crate** (option A). The zero-dep hand-roll (§9) was considered and rejected — it re-creates the data-loss-class per-pattern surface D1 exists to fix. The matcher-engine choice changes only the Cargo edits (#5/#6) and the internals of `path_is_ignored`; every other item in this plan is identical regardless.

### Simplification vs the baseline (the four axes asked)

| Axis | Baseline | This plan |
|---|---|---|
| **Workflow / control-flow** | two evaluations (route + a separate metrics matcher) | **one shared routine** `path_is_ignored`; `occ_route_metrics` calls it (its independent read+match deleted) |
| **Class / type** | new `GitignoreOracle` struct + `gitignore_oracle.rs` module | **none** — one inline free fn replacing the 3 deleted fns |
| **Files** | +1 new module file | **0 new files**; net-negative LOC in `dispatcher.rs` |
| **Fields** | possibly a cached matcher field | **0 new fields** — `LayerStackRouteProvider` stays `{ root }`; per-call rebuild is *load-bearing* for the required per-call re-read |

## 3. The route workflow after the change

```
OccService::prepare_changeset_with_base_hashes        (eos-occ/service.rs:231, UNCHANGED)
        │  per change
        ▼
LayerStackRouteProvider::is_ignored(path)             (dispatcher.rs ~1700)
        │  LayerStack::open(self.root)   ← per-call re-read PRESERVED (sees .gitignore edits between ops)
        ▼
path_is_ignored(&stack, path)  ◄── THE ONE SHARED ROUTINE (also called by occ_route_metrics)
        │
        │  ancestor dirs of `path`, root → leaf:  ""  → "a"  → "a/b" ...
        │  for each dir D:
        │     read  D/.gitignore  via stack.read_bytes  (snapshot = active merged manifest; absent ⇒ skip)
        │     build ignore::gitignore::Gitignore  ::new(D) + add_line(None, line)   ← per-level anchor
        │     ┌─ SEAL (caller-owned): is D's immediate child-dir on `path` ignored as a dir?
        │     │     yes ⇒ return TRUE now  (excluded dir seals subtree; deeper `!` cannot rescue)
        │     └─ else fold leaf verdict deeper-wins (Match::or): Ignore→true, Whitelist→false, None→keep
        ▼
   true → Route::Direct (LWW, gate bypassed)      false → Route::Gated (base-hash validated)
```

`occ_route_metrics` (`~1747`): open the stack **once**, skip `.git`/`.git/*`, call `path_is_ignored` per change → `direct_path_count` / `gated_path_count`. No second gitignore read.

## 4. The changes (diff table)

| # | File (crate) | Δ | What changes | Why |
|---|---|---|---|---|
| 1 | `eos-daemon/dispatcher.rs` | 🗑 | **Delete** `gitignore_matches` (~2357), `gitignore_rule_matches` (~2378), `wildcard_match` (~2397) (~65 LOC) | The three root-only / flat matchers are the bug. |
| 2 | `eos-daemon/dispatcher.rs` | ✚ | **Add** one free fn `path_is_ignored(stack: &LayerStack, path: &str) -> Result<bool, _>` (+ a tiny `matcher_for(dir)` helper): ancestor-dir snapshot walk, **per-level** `Gitignore` (`new(dir)`), caller-owned seal, deeper-wins fold | The correct oracle, inline beside its only callers. |
| 3 | `eos-daemon/dispatcher.rs` | ✎ | **Rewrite** `is_ignored` (~1700): `LayerStack::open(self.root)` (per-call re-read kept) → `path_is_ignored` → map err to `OccError::RoutePreparation` | Thin adapter over the shared fn. |
| 4 | `eos-daemon/dispatcher.rs` | 🗑✎ | **Rewrite** `occ_route_metrics` (~1747): drop the private root `.gitignore` read + inline match; open stack once, call `path_is_ignored` per non-`.git` change | One evaluation routine; telemetry can no longer diverge from routing. |
| 5 | `sandbox/Cargo.toml` | ✚ | Add `ignore = "0.4"` to `[workspace.dependencies]` *(option A only)* | Not present today (nor `globset`); brings the audited per-pattern engine. |
| 6 | `eos-daemon/Cargo.toml` | ✚ | Add `ignore.workspace = true` to `[dependencies]` *(option A only)* | Daemon owns the concrete provider (`service.rs:51-53`). |
| 7 | `eos-daemon/dispatcher.rs` `#[cfg(test)]` | ✎✚ | Extend `Fixture::new_with_gitignore` (~3818, writes one root `.gitignore`) to seed `.gitignore` at **multiple depths** (e.g. `&[(dir, contents)]`); add parity tests | Existing tests are root-only; the bug lives in nesting/depth/glob. |

**Net:** 0 new files, 0 new types, 0 new fields; `dispatcher.rs` non-test LOC roughly flat-to-negative; `eos-occ` untouched; trait signatures (`OccRouteProvider`, `CommitTransactionPort`) untouched.

### `path_is_ignored` correctness contract (why each piece is load-bearing)

| Guarantee | How it's met | Over-simplification that breaks it |
|---|---|---|
| **N2** dir-only at any depth | per-pattern via crate (implicit `**/` prefix) + parent walk | root-anchored prefix check (today's bug) |
| **N3** `*` not crossing `/` | crate builds globs with `literal_separator(true)` | reusing `wildcard_match` |
| **nested** `.gitignore` | read every ancestor `D/.gitignore`; one matcher per `D` (`new(D)`) | single flat builder ⇒ wrong anchoring; or root-only read |
| **`**`** | crate glob engine | hand-roll without `**` |
| **directory seal** | **our** top-down short-circuit: ignored ancestor dir ⇒ return true | trusting `matched*` alone (parent-unaware) |
| **snapshot source** | all reads via `stack.read_bytes` (active merged manifest) | `std::fs` on a projected tree |
| **per-call re-read** | rebuild matchers every call; **no** cached field | caching the matcher on the LRU-cached provider |

## 5. What stays exactly as-is (do not change)

- `eos-occ` crate entirely: `OccRouteProvider` / `CommitTransactionPort` traits, `OccService`, `prepare_changeset_with_base_hashes`, `AllGatedRouteProvider` stub.
- `LayerStackRouteProvider` struct shape `{ root: PathBuf }` and its `base_hash` method; `occ_service_for_root` LRU caching.
- The DIRECT/GATED gate mechanics (`validate_direct_group` / `validate_gated_group` / `revalidate_and_publish`), CAS retry, atomic-drop, auto-squash.
- `eos-runner/tool_primitives.rs` `wildcard_match`/`glob_matches`/`fnmatch` (separate copy backing grep/glob tooling — **out of scope**).

## 6. Final file / folder structure

```
sandbox/
├── Cargo.toml                              [workspace.dependencies] + ignore = "0.4"   (option A)
└── crates/
    ├── eos-occ/                            UNCHANGED
    └── eos-daemon/
        ├── Cargo.toml                      + ignore.workspace = true                   (option A)
        └── src/dispatcher.rs
              ├─ gitignore_matches / gitignore_rule_matches / wildcard_match   ── DELETED
              ├─ path_is_ignored  (+ matcher_for helper)                       ── NEW (inline)
              ├─ LayerStackRouteProvider::is_ignored                           ── thin adapter
              ├─ occ_route_metrics                                             ── rewired to path_is_ignored
              └─ #[cfg(test)] Fixture::new_with_gitignore + parity tests       ── extended
```
No new files.

## 7. Verification (success criteria)

New `#[cfg(test)]` parity tests in `dispatcher.rs` (seeded via the extended `Fixture`), each asserting the **route** (not just a consequence):

1. **N2** dir-only at non-root: root `.gitignore` `node_modules/` ⇒ `frontend/node_modules/x` routes **DIRECT**.
2. **N3** `*` not crossing `/`: `logs/*.log` ⇒ `logs/sub/x.log` routes **GATED** (not DIRECT-then-clobber).
3. **nested**: `frontend/.gitignore` `dist/` ⇒ `frontend/dist/x` DIRECT, root `dist/y` GATED.
4. **`**`**: `**/build/` (or `a/**/c`) matches across segments.
5. **`!` re-include** within a non-sealed dir.
6. **directory seal**: root `build/` + `build/.gitignore` `!keep.txt` ⇒ `build/keep.txt` stays **DIRECT/ignored**.
7. `occ_route_metrics` counts match the route decision for the same inputs (shared routine).
- Existing `root_gitignore_routes_target_as_direct` (~3667) and `occ_route_metrics_count_gated_and_direct_paths` (~3680) still pass.
- `cargo test -p eos-daemon` + `cargo clippy -p eos-daemon` clean. (Syscall-bound publish paths are compile-verified; routing is unit-testable in-process.)

## 8. Coordination / sequencing

Single-crate, single-file change (plus two one-line Cargo edits under option A). No edge to other Phase 2 lanes. Land in one commit; update `REPORT.html`/`REPORT.md` Phase 2 occ row `☐ → ☑` after green.

## 9. Alternatives considered

- **Zero-dep hand-roll (the `min-types-deps` proposal).** Keep deps flat by re-implementing the matcher on the already-present `regex`. *Rejected as default:* it re-derives N3 / `**` / dir-only-any-depth / `!`-ordering / nested-precedence / char-classes by hand — the exact surface that produced D1 — for a data-safety fix. It is *more* matcher code and higher correctness risk. Only preferable if a hard "no new daemon dependencies" policy applies. **Decided against (the `ignore` crate was chosen).**
- **Single flat `GitignoreBuilder` with `add_line(Some(dir), …)`** (the `min-files` proposal's recipe). *Rejected:* `from:` is provenance only; anchoring is `new(root)`, so one builder mis-anchors subdir patterns. Per-level matchers are required.
- **`globset` directly.** *Rejected:* gives globs but no gitignore semantics (dir-only, anchoring, `!` ordering, `**` precedence, seal) — we'd hand-roll the buggy layer on top.
- **New `gitignore_oracle.rs` module + `GitignoreOracle` struct** (baseline). *Rejected:* two callers, both in `dispatcher.rs`, no state to hold, per-call rebuild required — a module + type is pure overhead.
- **Cache the built matcher on the provider.** *Rejected:* provider is LRU-cached per root; caching would stop observing `.gitignore` edits between ops (breaks per-call re-read).
- **Char-class `[...]` fidelity.** Not in the required guarantee set; the `ignore` crate provides it for free, the hand-roll would omit it (acceptable gap, noted).

## 10. Notes / open questions

- **Snapshot vs live (occ.md Q3):** today routing reads the *active* merged manifest live (no version-pinned read API is wired into the provider). This plan preserves that — `path_is_ignored` reads the same source. Pinning the route to the prepared `snapshot_version` is a *separate, optional* hardening, not part of D1.
- `is_dir=false` is correct for OCC inputs (concrete change paths are files); dir-only patterns still fire via the parent walk / seal.
- Lenient parsing: a missing / non-UTF-8 / empty `.gitignore` contributes no lines ⇒ not-ignored ⇒ GATED (the safe, validated route) — matches today's `if !exists { Ok(false) }`.
