---
title: Adversarial Review Prompt — Manager Export Changes Spec
tags:
  - ephemeral-os
  - layerstack
  - manager
  - export
  - implementation-plan
  - adversarial-review
status: draft
reviews:
  - implementation_plan/export_changes/spec.md
---

# Adversarial Review Prompt — Manager Export Changes Spec

Use this prompt to run a hostile review of the **export_changes spec**
(`implementation_plan/export_changes/spec.md`) — a design document, not yet
implemented. Review the spec against the **codebase on `main` (working
tree)**: every factual claim the spec makes about existing code is
checkable today, and every design decision must survive contact with the
code it plans to reuse. You are not here to agree. Break the design on four
axes: **spec-vs-code truth, architecture cleanness / prefer-less, delta and
apply correctness, and security of the host boundary.** Assume every
"already exists", "precedent", "O(delta)", "idempotent", "reaped at boot",
and "cannot escape dest" claim is marketing until proven.

A finding against a spec is still a finding: the fix is a spec change
(different design, added invariant, honest cost table, promoted deferral),
not code. "The spec is silent on X and X can corrupt a host directory" is a
valid — and severe — finding.

## Operating rules

1. **Verify, don't trust.** Every `path:line` the spec cites, every
   "precedent", every "the primitive already exists" must be confirmed by
   reading the actual file on `main`. If the code contradicts the spec,
   that is a finding against the spec. Cite `path:line` for everything.
2. **Attack the seams, not the center.** The happy path (small delta, fresh
   dest, one chunk) is not interesting. Hunt the edges: hostile tar entries
   consumed by a host process, filename collisions with whiteout encoding,
   the three coexisting merged-view implementations, daemon restart between
   chunks, a 10 GB delta against a per-request timeout, two operators
   exporting the same sandbox concurrently.
3. **One finding = one falsifiable claim + evidence + severity + fix.**
   Severity: Critical (host filesystem compromise outside dest, or silent
   corruption/data loss inside dest) / High (wrong exported content, a
   stuck or unrecoverable operation, or a spec factual claim contradicted
   by code) / Medium (design smell or unverified claim that will rot) /
   Low (nit). No vibes.
4. **Prefer the destructive test.** For each risk, state the concrete layer
   stack, tar entry sequence, timing, or operator action that triggers it.
   Use the spec's own vocabulary: manifests newest-first like
   `[L2 L1 B]`, whiteouts `wh(p)`, opaque `opq(d)`. "Could break" without
   a trigger is not a finding.
5. **Take positions.** Where the spec chose between alternatives (or never
   named the alternative), argue one side with evidence. A deferral the
   spec cannot defend belongs in v1; a v1 feature it cannot defend belongs
   deleted.

## Design under review (as claimed)

- **Manager-owned**: `sandbox-manager-cli export_changes --sandbox-id ID
  --dest PATH [--format dir|tar|tar-zst]`, spec'd in
  `sandbox-manager-operations` (management family), dispatched like
  `checkpoint_squash` (`operation/cli_definition/management_operations.rs`,
  impl in `operation/management/service/impls/`).
- **Two daemon-local runtime ops, both `cli: None`**: `export_layerstack`
  (lease → newest-wins winner fold over non-base layers → one tar-zst spool
  under `<scratch_root>/.export/`) and `read_export_chunk` (base64 frames
  ≤ 2 MiB raw, unlink-on-eof). The squash_layerstack registration
  precedent (`operation/src/layerstack/service/impls/squash.rs`).
- **One wire format**: always tar-zst with logical `.wh.` whiteouts and
  `.wh..wh..opq` opaque markers; `--format` selects a manager-side
  rendering only. The manager applier (`sandbox-manager/src/export_apply.rs`,
  planned) applies dir mode: ensure-dir, skip-unchanged by (size, mtime),
  mtime stamping, whiteout deletion, opaque clear-directory.
- **Claimed costs**: enumerate O(Σ delta-layer entries); content read
  O(merged delta bytes); re-export host writes O(new bytes); daemon
  intermediate O(compressed delta); memory O(unique changed paths).
- **Claimed invariants** (spec §Vocabulary and invariants, 1–8): read-only
  storage, merged-delta equivalence with `MergedView`, lease pinning,
  idempotent re-run, published-only, detach preserved / manager sole host
  writer, archive atomicity, no fsync.
- **Key reuse claims**: `MergedView`/`apply_layer`
  (`layerstack/src/stack/projection/{mod,apply}.rs`) already encode the
  masking and apply semantics; the bind detach at
  `operation/src/services.rs:84` forces gateway streaming; the 8 MiB
  result-envelope precedent; `acquire_snapshot` leases; boot reap covers
  spool leftovers.

## Axis 1 — Spec-vs-code truth

Probe and answer with evidence:

1. **Every cited line.** `services.rs:84` detach (and its panic-on-failure
   at `services.rs:239-272`), `projection/mod.rs:205` `project`,
   `apply_layer` whiteout/opaque handling, `router/forward.rs` Ready gate
   and `REQUEST_READ_TIMEOUT_S`, `impls/squash.rs` `cli: None` shape,
   `InvalidWorkspaceRoot` in `management_operations.rs`. Any drift between
   spec citation and code is a finding.
2. **"Reaped with scratch at boot."** The spec asserts spool leftovers
   under `<scratch_root>/.export/` are cleaned by boot reap. Read the
   actual boot reap (`workspace/src/lifecycle/persistence.rs`,
   `operation/src/services.rs::boot_reap_then_sweep`): does anything sweep
   scratch paths that are not session run dirs? If not, the spec ships a
   permanent leak and the claim is false.
3. **The base predicate.** The spec identifies the base as "every `B*`
   layer". Check `layerstack` layer-id construction and the shared-base
   design (`o1_shared_workspace_base.md`, create with `--count N`): is
   exactly one B layer guaranteed at the bottom, always? Can a manifest
   ever interleave or omit it? What does the fold export if the predicate
   is wrong?
4. **Manifest ordering.** The fold depends on `manifest.layers` being
   newest-first (spec cites `read_entry` early-return and `project`'s
   `.rev()`). Confirm, and confirm nothing (squash commit, amend) can
   produce a differently-ordered manifest.
5. **The 8 MiB envelope and timeouts.** The spec sizes chunks against "the
   8 MiB result-envelope precedent" and rides `invoke_with_timeout` at
   `REQUEST_READ_TIMEOUT_S`. Find the real constants (value and unit of
   `REQUEST_READ_TIMEOUT_S`, any gateway/daemon body caps). Then do the
   math the spec never does: a 1 GB compressed delta at 2 MiB per
   round-trip is ~500 sequential forwards — what is the realistic
   wall-clock, and does the *start* request (whole fold + spool) fit one
   `REQUEST_READ_TIMEOUT_S` for a plausibly large delta? "Squash first" is
   hand-waving; quantify where it breaks.
6. **Dependency claims.** `tar` in `[workspace.dependencies]`; `zstd`
   absent (spec adds it); base64 — the spec hedges "or the existing
   payload-encoding dep". Resolve the hedge: what does the runner result
   path actually use for base64, and can the manager reuse it?

## Axis 2 — Architecture cleanness / prefer-less

Probe and answer with evidence:

1. **Three merged-view implementations.** After this spec ships, the truth
   of "what does the stack look like merged" lives in: `MergedView`
   (reads), squash `flatten` (staging trees), and the new winner fold
   (export). Invariant 2 pins fold↔`MergedView` by test — nothing pins
   fold↔`flatten`. Is a third implementation defensible, or should the
   spec be forced to extract one shared fold (and does the squash spec's
   own history — "no shared abstraction forced before it's needed" — cut
   for or against here)? Take a position.
2. **The unbuilt alternatives.** The spec never argues against two obvious
   transports it does not use: (a) `SandboxRecord.daemon_http`
   (`management_operations.rs` serializes a `daemon_http` endpoint; hyper
   is a workspace dep) — an HTTP stream would kill both the base64 tax and
   the ~500-round-trip loop; (b) the manager owns Docker — a direct
   volume/archive download (`upload_archive`'s missing twin) bypasses the
   daemon protocol entirely. For each: what does it exist for today, why
   is JSON chunk paging still the right v1, and is the spec's silence
   defensible? If the daemon-consistency argument (leases, torn reads
   under squash GC) is the real justification for rejecting (b), the spec
   must say so.
3. **Manager charter creep.** `sandbox-manager` gains a filesystem
   renderer (`export_apply.rs`: tar semantics, whiteout application,
   skip-unchanged). Is that inside the manager's one-sentence job, or does
   it belong in a narrower crate the manager consumes? Where do
   README boundary law and the "crate-root engine module"
   (`daemon_install.rs`) precedent actually land?
4. **Spool registry state.** `export_layerstack`/`read_export_chunk` need
   shared mutable state ({export_id → spool path, total}) inside the
   operation crate's layerstack service. Name its owner, its lock, its
   lifetime across daemon restart, and whether an in-memory registry whose
   entries die with the daemon (chunks then fail) is reported honestly in
   the spec's failure story.
5. **Result-line assembly.** The manager merges daemon stats with apply
   stats into one JSON line. Check the squash output-contract philosophy
   (counts only, no byte totals, observability owns detail): does
   `bytes_written`/`skipped_unchanged` in the result line violate the
   house rule the same spec cites, or is "this run's work" genuinely
   different from "current state"? Be consistent — flag any field that is
   stale or unbounded.
6. **Surface honesty.** `dest`/`format` ride the manager request but are
   consumed manager-side; `read_export_chunk` is invisible; the runtime
   CLI gains nothing. Confirm the catalog/dispatch parity story holds
   (does any existing test enforce spec↔dispatcher parity in
   `sandbox-manager`?) and that a spec-only + dispatcher-later merge order
   cannot ship a listed-but-undispatchable operation.

## Axis 3 — Delta and apply correctness

Probe and answer with evidence. Construct the layer stacks.

1. **Whiteout-name collision.** A sandbox legitimately publishes a file
   literally named `.wh.config` (layer content, not a marker). The spool
   encodes deletions as logical `.wh.<name>` entries. How does the applier
   distinguish "delete config" from "write a file named .wh.config"? Check
   how capture/publish (`whiteout.rs`, `LOGICAL_WHITEOUT_PREFIX`) handles
   user files with that prefix today. If the encoding is ambiguous, the
   export silently deletes the wrong thing — rate it accordingly and
   prescribe the fix (escaping, a PAX attribute, or reject-at-publish).
2. **Opaque ordering under streaming apply.** The applier streams entries
   in tar order and the spec relies on `cfg/.wh..wh..opq` clearing before
   `cfg/prod.yml` applies. Prove the emit order (BTreeMap path order + the
   marker's `.` sort rank) guarantees clear-before-children for every
   nesting, including `opq` on a parent and winners in a grandchild, and
   an `opq` directory that is itself under a deleted directory. If order
   is load-bearing, the spec must state it as an invariant of emit, not an
   accident.
3. **Metadata fidelity.** The fold emits `Directory` winners; the applier
   "ensures" directories. Where do directory modes go? A sandbox `chmod
   700` on an existing base directory copies it up — does the export
   apply the mode, or silently leave the host's? Same question for file
   modes (spec says entries carry mode) vs ownership (uid/gid — the
   daemon runs as?) vs xattrs vs hardlinks between winners (squash
   flatten hardlinks — do two winner paths share an inode, and does tar
   emit them as hardlink entries or duplicate content?). Every "not
   carried" answer must appear in the spec's honesty boundary.
4. **skip-unchanged soundness.** (size, mtime) with tar's second-granular
   mtime: construct the false-skip — export, then a same-second,
   same-size content change published and re-exported. Also the reverse:
   does `File::set_times` after write set what tar preserved (nanoseconds
   truncated?), making every re-export re-copy everything (skip never
   fires) or skip wrongly? One of the two failure modes exists unless the
   spec pins the mtime round-trip precision.
5. **Idempotency vs invariant 4.** "Re-run writes zero content bytes" —
   but deletions: re-applying `.wh.b.rs` when `b.rs` is already gone,
   opaque clears re-clearing (removing files the *host* added since —
   which B1's fidelity condition permits): is the second run truly
   byte-identical, and do `deletes_applied`/`opaque_clears` counts lie on
   re-runs?
6. **The amend hole.** B3's honesty boundary says only `amend_path` can
   make a path leave the delta unmasked. Verify against
   `impls/amend.rs` / publish internals: can a squash of
   [L-with-file, L-with-whiteout] blocks, GC, or capture eviction also
   produce a delta where a previously-exported path silently vanishes?
   Every such producer widens the stale-dest hole the spec pins on amend
   alone.
7. **Symlink semantics.** A winner symlink replacing a host directory
   (and vice versa); a dangling symlink; a symlink whose target contains
   `..`. Does the applier's replace logic (`remove_path` + recreate)
   match `apply_layer`'s, and is following ever possible during the
   ensure-dir walk?

## Axis 4 — Security and failure of the host boundary

This axis outranks the others. The applier is a **host process with
manager privileges consuming bytes authored inside the sandbox**. A
compromised sandbox daemon is in scope: it controls every tar entry, every
chunk, and every count in the start response.

1. **Tar-slip, all variants.** Entry names with `..` or absolute paths;
   an entry that writes a symlink `dest/x → /etc` followed by
   `x/passwd`; a `.wh.` name with traversal (`.wh.../../y`); a hardlink
   entry pointing outside dest; case-insensitive-filesystem collisions on
   macOS hosts. The spec's only defense in print is ensure-dir replacing
   symlinks at directory positions — prove whether that closes the
   symlink-then-traverse race or only the pre-existing-symlink case. The
   spec must name its canonicalization/no-follow strategy (open-parent
   `O_NOFOLLOW` fd-walk, reject `..`/absolute/hardlink entries) or this is
   Critical.
2. **Resource bombs.** zstd bomb (6 MiB spool → TBs decompressed): dir
   mode streams to files (host disk exhaustion at operator-chosen dest —
   acceptable?), tar mode decompresses wholesale — bounded by what?
   Daemon-claimed `total`/`len`/`spool_bytes`: does the manager allocate
   or loop on them unvalidated? Entry count bombs (millions of empty
   files)? State the caps the spec needs.
3. **Dest guard scope.** `--dest /` with format dir is currently legal:
   absolute, a directory. The delta then deletes and overwrites at
   filesystem root with manager privileges. Is "operator authority" an
   acceptable answer, or does the guard need a deny-list (`/`, `$HOME`,
   manager state dirs, path-inside-spool)? Compare `create_sandbox`'s
   validation depth for its host path. Take a position.
4. **Concurrent operators / stale spools.** Two `export_changes` for the
   same sandbox: second `export_layerstack` replaces the spool while the
   first is mid-paging. Spec says the manager loop is synchronous "so
   overlapping readers do not arise from this design" — that claim is
   about one manager invocation; two invocations exist. What does the
   first reader observe (EOF? garbage? export_id mismatch?) and does the
   spec's singleflight actually serialize across both, or only across
   folds?
5. **Death at every arrow.** Daemon dies mid-spool (lease? spool
   half-written?); daemon restarts between chunks (in-memory registry
   gone — error surfaced how?); manager dies mid-apply (partial dest —
   invariant 4 story holds only for dir; what about a half-written
   `.tmp` archive?); gateway timeout on the start request while the
   daemon keeps spooling (orphan spool + singleflight held?). For each:
   what does the operator see, and what converges it?
6. **The detach invariant, re-checked.** Invariant 6 claims no
   runtime-CLI-reachable surface can write to the host. `read_export_chunk`
   is `cli: None` — but is it reachable by name through
   `sandbox-runtime-cli` anyway (does the CLI reject unknown-catalog ops
   client-side, or forward any op string to the daemon)? Check
   `run_request_from_catalog`: if name-dispatch works from the runtime
   CLI, the export ops are runtime-reachable after all — the data still
   only flows to the caller, but the spec's "manager-owned, runtime gains
   nothing" claim needs re-wording, and spool lifecycle becomes
   runtime-drivable (spool replacement as a nuisance op).

## Required output

Produce, in this order:

1. **Verdict table** — one row per axis (Truth / Architecture /
   Correctness / Security), each `PASS | PASS-WITH-RISKS | FAIL`, one-line
   justification.
2. **Findings** — sorted by severity. Each: `[SEV] title` · falsifiable
   claim · evidence (`path:line` or constructed trigger) · concrete
   trigger · recommended spec change.
3. **Top 3 must-fix** before implementation may start.
4. **What the spec oversells** — every "already exists" / "precedent" /
   "O(...)" / "reaped at boot" / "cannot escape dest" claim not fully
   backed by code or analysis.
5. **One question** you could not resolve from the code that a human must
   answer.

Do not soften. If the design is sound, say so in one line and spend the
rest on the sharpest risks anyway — the host-side tar applier consuming
sandbox-authored bytes, the whiteout-name ambiguity, and the unverified
boot-reap claim are the most promising places to dig.
