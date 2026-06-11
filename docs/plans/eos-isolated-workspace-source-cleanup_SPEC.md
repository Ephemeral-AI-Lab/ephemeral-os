# eos-isolated-workspace Source Cleanup SPEC

Status: Proposed
Date: 2026-06-11
Owner: sandbox/crates
Scope: `sandbox/crates/eos-isolated-workspace/src`,
`sandbox/crates/eos-isolated-workspace/tests`, and direct `eos-daemon`
callers required to preserve the public facade.

## 1. Goal

Aggressively simplify `eos-isolated-workspace` source structure while keeping
the crate's ownership boundary intact: isolated workspace lifecycle, namespace
envelope wiring, network setup, persistence/recovery, and no publish path.

This is a source-shape and API-surface cleanup. The required outcome is a
directory-module layout with one public facade (`IsolatedManager`), no
same-name file-plus-folder module pairs, fewer implementation files, and a
measurable source LOC reduction.

## 2. Baseline

Measured on 2026-06-11:

```sh
find sandbox/crates/eos-isolated-workspace/src -type f -name '*.rs' | sort
find sandbox/crates/eos-isolated-workspace/src -type f -name '*.rs' -print0 | xargs -0 wc -l | sort -n
find sandbox/crates/eos-isolated-workspace -name '.DS_Store' -print
```

Baseline:

- Rust source files under `src`: 18
- Rust source LOC under `src`: 3,078
- Junk source-tree files:
  - `sandbox/crates/eos-isolated-workspace/.DS_Store`
  - `sandbox/crates/eos-isolated-workspace/src/.DS_Store`

Largest current implementation files:

| File | LOC | Issue |
| --- | ---: | --- |
| `sessions/lifecycle.rs` | 351 | Correct owner, but setup/teardown timing and inspection code are repetitive. |
| `network/netfilter/wire.rs` | 340 | Correct protocol boundary; keep split. |
| `namespace.rs` | 319 | Hybrid `namespace.rs` + `namespace/` layout. |
| `network.rs` | 314 | Hybrid `network.rs` + `network/` layout. |
| `network/netfilter/exprs.rs` | 303 | Correct protocol-expression boundary; keep split. |
| `network/rtnl.rs` | 187 | Correct rtnetlink boundary; keep split. |
| `namespace/ns_runner.rs` | 184 | Name is too implementation-shaped and lives under a hybrid parent. |
| `sessions/gc.rs` | 170 | Recovery/persistence teardown logic split away from persisted rows. |
| `sessions.rs` | 165 | Public `IsolatedSessions` competes with `IsolatedManager`. |
| `manager.rs` | 85 | Pure delegation wrapper over `IsolatedSessions`. |

## 3. Non-Goals

- Do not merge `eos-isolated-workspace` into `eos-daemon`, `eos-namespace`,
  `eos-overlay`, or `eos-layerstack`.
- Do not add layer-stack storage ownership, lease acquisition, publish, OCC, or
  checkpoint behavior to this crate.
- Do not change daemon RPC response field names, persisted `manager.json`
  schema, timing phase names, veth/cgroup naming, or workspace root semantics.
- Do not keep compatibility modules solely for old internal paths.
- Do not use a `network.rs` plus `network/` hybrid layout.
- Do not use a `namespace.rs` plus `namespace/` hybrid layout.
- Do not collapse `network/netfilter/wire.rs` into expression-building code;
  raw nftables netlink wire encoding is a real protocol boundary.

## 4. Required Target Source Tree

Acceptance requires this exact source tree shape under
`sandbox/crates/eos-isolated-workspace/src`:

```text
src/
  lib.rs
  caps.rs
  error.rs
  manager/
    mod.rs
    capacity.rs
    handle.rs
    lifecycle.rs
    recovery.rs
  namespace/
    mod.rs
    runner_child.rs
  network/
    mod.rs
    rtnl.rs
    netfilter/
      mod.rs
      exprs.rs
      wire.rs
```

Required moves and deletions:

| Current path | Target |
| --- | --- |
| `src/manager.rs` | fold into `src/manager/mod.rs` as the real facade |
| `src/sessions.rs` | fold into `src/manager/mod.rs` |
| `src/sessions/capacity.rs` | `src/manager/capacity.rs` |
| `src/sessions/handle.rs` | `src/manager/handle.rs` |
| `src/sessions/lifecycle.rs` | `src/manager/lifecycle.rs` |
| `src/sessions/gc.rs` | `src/manager/recovery.rs` |
| `src/sessions/persistence.rs` | fold into `src/manager/recovery.rs` |
| `src/sessions/resources.rs` | fold into `src/manager/lifecycle.rs` or `src/manager/handle.rs` |
| `src/namespace.rs` | `src/namespace/mod.rs` |
| `src/namespace/ns_runner.rs` | `src/namespace/runner_child.rs` |
| `src/network.rs` | `src/network/mod.rs` |
| `src/network/rtnl.rs` | keep |
| `src/network/netfilter/mod.rs` | keep |
| `src/network/netfilter/exprs.rs` | keep |
| `src/network/netfilter/wire.rs` | keep |
| `.DS_Store` files in this crate | delete |

The following paths must not exist after the refactor:

```text
src/manager.rs
src/sessions.rs
src/sessions/
src/namespace.rs
src/namespace/ns_runner.rs
src/network.rs
```

## 5. Public API Shape

`IsolatedManager` is the public daemon-facing lifecycle facade. It owns the
state currently stored in `IsolatedSessions`.

Required crate-root exports:

```rust
pub use caps::{ResourceCaps, Rfc1918Egress};
pub use error::IsolatedError;
pub use manager::{
    ExitOutcome, IsolatedManager, IsolatedSnapshot, IsolatedWorkspaceId, WorkspaceHandle,
};
```

Required API cleanup:

- Remove public `IsolatedSessions`.
- Keep `IsolatedManager::with_scratch_root` as the daemon construction entry.
- Keep a test-only or crate-private stubbed constructor for unit tests.
- Do not expose internal recovery, lifecycle, capacity, namespace, or network
  module paths from the crate root.
- Remove unused `ResourceCaps::sample_interval_s` from this runtime crate. If
  the wider sandbox config still keeps `sample_interval_s`, it must stop mapping
  that field into `ResourceCaps`.

## 6. LOC Budget

The baseline is 3,078 source LOC under `src`.

Hard acceptance target:

- Rust source files under `src`: **15 or fewer**
- Rust source LOC under `src`: **2,850 or fewer**
- Required LOC reduction from baseline: **at least 228 LOC** (`7.4%`)
- No `.DS_Store` or other non-Rust junk files under this crate

Stretch target:

- Rust source files under `src`: **14 or fewer**
- Rust source LOC under `src`: **2,750 or fewer**
- Required LOC reduction from baseline: **at least 328 LOC** (`10.7%`)

This is a final-code target, not a net-diff target. Pure file moves without
deleting the delegation layer, dead config fields, repeated timing plumbing, and
stale comments do not satisfy the spec.

Expected reduction sources:

| Cleanup | Expected LOC drop |
| --- | ---: |
| Replace delegating `IsolatedManager` + public `IsolatedSessions` with one real `IsolatedManager` | 70-95 |
| Remove unused default-root constructor and unused config/runtime field mapping | 15-35 |
| Fold resources helpers into their owning modules | 10-25 |
| Merge GC and persistence into `manager/recovery.rs` | 15-35 |
| Simplify setup/teardown phase timing and remove needless clone/DNS return plumbing | 30-55 |
| Trim stale migration comments and redundant module ceremony | 50-100 |

## 7. Implementation Phases

### Phase 1: Facade and Module Tree

- Move `sessions.rs` into `manager/mod.rs`.
- Move `sessions/{capacity,handle,lifecycle}.rs` into `manager/`.
- Merge `sessions/gc.rs` and `sessions/persistence.rs` into
  `manager/recovery.rs`.
- Fold `sessions/resources.rs` into lifecycle/handle ownership.
- Delete public `IsolatedSessions`; update `eos-daemon` to store and call
  `IsolatedManager` directly as the real facade.
- Keep API behavior stable for enter, exit, status, list, touch, TTL sweep, and
  orphan reaping.

### Phase 2: Directory Modules

- Move `namespace.rs` to `namespace/mod.rs`.
- Rename `namespace/ns_runner.rs` to `namespace/runner_child.rs`.
- Move `network.rs` to `network/mod.rs`.
- Keep `network/rtnl.rs`.
- Keep `network/netfilter/{mod,exprs,wire}.rs`.
- Remove stale path references and parent-file module declarations.

### Phase 3: Dead Surface and LOC Reduction

- Remove `ResourceCaps::sample_interval_s` from this runtime crate and the
  daemon mapping into it.
- Remove unused public constructors that are not used by daemon or tests.
- Change DNS configuration plumbing to return `Result<()>` unless the applied
  fallback result is surfaced in an accepted response.
- Remove needless `layer_paths` clone in lifecycle wiring.
- Add a small local timing helper only if it deletes repeated phase blocks.
- Delete stale migration comments that describe old shell or porting state
  rather than current invariants.
- Delete crate-local `.DS_Store` files.

## 8. Acceptance Criteria

### 8.1 Resulting File and Folder Structure

The refactor is accepted only if the final `src` tree is exactly this module
shape:

```text
sandbox/crates/eos-isolated-workspace/src/
  lib.rs
  caps.rs
  error.rs
  manager/
    mod.rs
    capacity.rs
    handle.rs
    lifecycle.rs
    recovery.rs
  namespace/
    mod.rs
    runner_child.rs
  network/
    mod.rs
    rtnl.rs
    netfilter/
      mod.rs
      exprs.rs
      wire.rs
```

No parent-file plus same-name-folder module pairs may remain. In particular,
`network.rs + network/`, `namespace.rs + namespace/`, and
`sessions.rs + sessions/` are rejected final states.

Tree-shape gate:

```sh
test -f sandbox/crates/eos-isolated-workspace/src/manager/mod.rs
test -f sandbox/crates/eos-isolated-workspace/src/manager/capacity.rs
test -f sandbox/crates/eos-isolated-workspace/src/manager/handle.rs
test -f sandbox/crates/eos-isolated-workspace/src/manager/lifecycle.rs
test -f sandbox/crates/eos-isolated-workspace/src/manager/recovery.rs
test -f sandbox/crates/eos-isolated-workspace/src/namespace/mod.rs
test -f sandbox/crates/eos-isolated-workspace/src/namespace/runner_child.rs
test -f sandbox/crates/eos-isolated-workspace/src/network/mod.rs
test -f sandbox/crates/eos-isolated-workspace/src/network/rtnl.rs
test -f sandbox/crates/eos-isolated-workspace/src/network/netfilter/mod.rs
test -f sandbox/crates/eos-isolated-workspace/src/network/netfilter/exprs.rs
test -f sandbox/crates/eos-isolated-workspace/src/network/netfilter/wire.rs
test ! -e sandbox/crates/eos-isolated-workspace/src/manager.rs
test ! -e sandbox/crates/eos-isolated-workspace/src/sessions.rs
test ! -e sandbox/crates/eos-isolated-workspace/src/sessions
test ! -e sandbox/crates/eos-isolated-workspace/src/namespace.rs
test ! -e sandbox/crates/eos-isolated-workspace/src/namespace/ns_runner.rs
test ! -e sandbox/crates/eos-isolated-workspace/src/network.rs
test -z "$(find sandbox/crates/eos-isolated-workspace -name '.DS_Store' -print)"
```

### 8.2 LOC Reduction Goal

The baseline is **3,078 LOC** across **18 Rust source files** under
`sandbox/crates/eos-isolated-workspace/src`.

Hard acceptance:

- Final Rust source files under `src`: **15 or fewer**
- Final Rust source LOC under `src`: **2,850 or fewer**
- Required LOC drop: **at least 228 LOC**

Stretch acceptance:

- Final Rust source files under `src`: **14 or fewer**
- Final Rust source LOC under `src`: **2,750 or fewer**
- Required LOC drop: **at least 328 LOC**

LOC and file-count gate:

```sh
files=$(find sandbox/crates/eos-isolated-workspace/src -type f -name '*.rs' | wc -l | tr -d ' ')
test "$files" -le 15

loc=$(find sandbox/crates/eos-isolated-workspace/src -type f -name '*.rs' -print0 | xargs -0 wc -l | tail -1 | awk '{print $1}')
test "$loc" -le 2850
```

Public API gates:

```sh
rg -n "pub use .*IsolatedSessions|pub struct IsolatedSessions|IsolatedSessions" \
  sandbox/crates/eos-isolated-workspace/src \
  sandbox/crates/eos-daemon/src

rg -n "sample_interval_s" \
  sandbox/crates/eos-isolated-workspace/src \
  sandbox/crates/eos-daemon/src/services/workspace.rs
```

The first command may only match historical comments if explicitly kept; the
target is no production use or public export. The second command must not match
inside `eos-isolated-workspace` or daemon runtime mapping.

Build and behavior gates:

```sh
cargo metadata --manifest-path sandbox/Cargo.toml --format-version 1 --no-deps
cargo check --manifest-path sandbox/Cargo.toml -p eos-isolated-workspace --all-targets
cargo test --manifest-path sandbox/Cargo.toml -p eos-isolated-workspace
cargo clippy --manifest-path sandbox/Cargo.toml -p eos-isolated-workspace --all-targets -- -D warnings
cargo check --manifest-path sandbox/Cargo.toml -p eos-daemon --all-targets
git diff --check
```

## 9. Review Notes

- Prefer deleting the duplicate public facade over preserving compatibility
  paths. The public type after this refactor is `IsolatedManager`.
- Keep network and namespace as directory modules because the user explicitly
  rejected the `network.rs + network/` and `namespace.rs + namespace/` shape.
- Do not collapse the netfilter wire encoder into expression construction for
  file-count optics. That split is a real implementation boundary.
- If a broader dependency cleanup is attempted, verify whether
  `eos-isolated-workspace` can depend on namespace protocol DTOs without
  pulling the Linux namespace runner's overlay/layer-stack dependencies.
