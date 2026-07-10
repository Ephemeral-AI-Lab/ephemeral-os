# Spec: `sandbox-runtime-workspace` profile-vocabulary rename + cleanup

Status: **completed and archived** — retained as rename-history evidence
Scope: `crates/sandbox-runtime/workspace/src` + cross-crate call sites in
`operation`, `daemon`, `observability`.
Depends on: commit `10b70de64` (`NetworkProfile` / `shared` rename)
Behavior change: **none** — renames, dead-code/duplicate removal, and an
append-only DB column rename that preserves existing data.

> Paths, line numbers, and imperative wording below record the completed
> rename as it was planned and landed. Operation-ownership references are
> translated to the current registry; the rest is historical evidence, not
> current ownership guidance.

## Overview

After `WorkspaceProfile` → `NetworkProfile`, "profile" is now used two
incompatible ways inside the workspace crate:

1. **`NetworkProfile`** — the *one* real profile axis: `Shared` (join host net ns)
   vs `Isolated` (private net ns + veth). **Correct — keep it.**
2. **Everything else with "Profile"** — `profile/`, `WorkspaceProfileManager`,
   `WorkspaceProfileHandle`, `WorkspaceProfileFds`, `WorkspaceProfileError`,
   `enter_with_profile`, `active_profile_id`, `profile_*` locals. None are
   profile-specific; they manage the **live, mounted workspace** (overlay + holder
   namespace stack + lease + optional veth) for *both* network modes.

This spec (a) renames group (2) to manager/mounted-workspace vocabulary, (b) does
the `profile` field → `network` rename, (c) renames `isolated_setup/` →
`isolated_network_setup/`, (d) folds in three safe dead-code/duplicate removals,
and (e) migrates the observability DB column `profile` → `network_profile`.

### Mental model

- Both modes always create a private workspace overlay + a holder namespace stack
  (mount/pid/user). `Isolated` additionally builds a private network (veth +
  policy); `Shared` joins the host net ns. That single axis is `NetworkProfile`.
- The manager owns admission policy, persistence, and a map of live mounted
  workspaces keyed by `WorkspaceSessionId`.

---

## Resolved decisions

1. **Scope** — **both** Tier A (vocabulary) **and** Tier B (`profile` field →
   `network`).
2. **`WorkspaceProfileHandle` →** **`MountedWorkspace`** (emphasizes: overlay
   mounted + holder up; distinct from the external lightweight `WorkspaceHandle`).
3. **`enter`/`exit` → `open`/`close`** — adopt the `open`/`close` pair (matches the
   existing `NotOpen` state; avoids the `WorkspaceEntry`/setns "enter" collision).
4. **`profile/handle.rs` → `session/state.rs`** (file rename).
5. **`isolated_setup/` → `isolated_network_setup/`** — names the network-specific
   bridge/veth/nftables setup the `Isolated` mode requires (vs the vague "setup of
   what?").
6. **Observability DB schema** — migrate it: rename the `workspace_snapshots.profile`
   column (+ DTO/record/row/JSON surfaces) to network-profile vocabulary via a new
   append-only migration (see "Observability DB schema migration").

---

## Verb correction (why `open`, not `enter`)

`enter_with_profile` is a factory: it mints a fresh `WorkspaceSessionId`, creates
overlay scratch, spawns the holder, opens ns fds, sets up isolated network, mounts
the overlay, registers, persists (`lifecycle/create.rs:77-124`). It is not an
"enter". This crate already reserves enter/entry for *a process joining a live
workspace's namespaces* (`WorkspaceEntry`, `WorkspaceHandle::entry()`,
`WorkspaceEntryFds`, `namespace/setns_runner.rs`). The crate's own error vocabulary
is open/closed (`NotOpen` → `"workspace session is not open"`), so the lifecycle
pair is **`open`** (create + bring up) / **`close`** (teardown). `ExitOutcome`
stays — it names the holder-exit teardown result, orthogonal to the verb.

---

## Name map (Tier A + B)

| Kind | Before | After |
|---|---|---|
| Module dir | `profile/` | `session/` |
| Module dir | `isolated_setup/` | `isolated_network_setup/` |
| File | `profile/handle.rs` | `session/state.rs` |
| File | `profile/manager.rs` | `session/manager.rs` |
| File | `profile/mod.rs` | `session/mod.rs` |
| Type | `WorkspaceProfileManager` | `WorkspaceManager` |
| Type | `WorkspaceProfileHandle` | `MountedWorkspace` |
| Type | `WorkspaceProfileFds` | `HolderNsFds` |
| Type | `WorkspaceProfileError` | `WorkspaceManagerError` |
| Method | `enter_with_profile` | `open` |
| Method | `exit` | `close` |
| Fn | `active_profile_id` | `active_session_id` |
| Fn | `workspace_error_from_profile_error` | `workspace_error_from_manager_error` |
| Local | `profile_id` | `session_id` |
| Local | `profile_handle` | `session` |
| Local | `profile_snapshot` | `snapshot` |
| Field (Tier B) | `.profile: NetworkProfile` on `WorkspaceHandle`, `MountedWorkspace`, `WorkspaceLaunchContext`, `CreateWorkspaceRequest`, `*_for_test` params | `.network` |

Module/`//!` docs in `lib.rs`, `session/mod.rs`, `session/manager.rs` that say
"isolation profiles"/"profiles" → reword to "network modes".

---

## Production change inventory

### Definitions

| File:line | Item |
|---|---|
| `lib.rs:17,22` | `mod isolated_setup;` → `mod isolated_network_setup;`; `pub mod profile;` → `pub mod session;` |
| `session/mod.rs:6-13` | re-export renamed types from `manager`/`state` |
| `session/manager.rs:47` | `enum WorkspaceProfileError` → `WorkspaceManagerError` (`impl` :61) |
| `session/manager.rs:63-70` | **delete** `WorkspaceManagerError::kind()` — dead (zero callers) |
| `session/manager.rs:72` | `struct WorkspaceProfileManager` → `WorkspaceManager` (`impl` :81) |
| `session/manager.rs:78` | `HashMap<WorkspaceSessionId, WorkspaceProfileHandle>` → `…, MountedWorkspace>` |
| `session/state.rs:8` | `struct WorkspaceProfileHandle` → `MountedWorkspace`; field `profile` → `network` (:11) |
| `session/state.rs:28` | `struct WorkspaceProfileFds` → `HolderNsFds` (`impl` :35) |

### Methods / fns / locals

| File:line | Change |
|---|---|
| `lifecycle/create.rs:77` | `enter_with_profile` → `open`; param `profile`→`network`; returns `MountedWorkspace` |
| `lifecycle/create.rs:17,95` | `handle.profile` → `handle.network` read + field init |
| `lifecycle/create.rs:133` | **delete** `record_create_phase_ms`; call sites :27,33,41,65 use `super::record_phase_ms` |
| `lifecycle/create.rs:11,50,56,71,126` | retype `WorkspaceProfileManager`/`&WorkspaceProfileHandle` |
| `lifecycle/destroy.rs:91` | `pub fn exit` → `pub fn close` (caller: `destroy_workspace.rs:20`) |
| `lifecycle/destroy.rs:81` | `handle.profile` → `handle.network` |
| `lifecycle/destroy.rs:26,29,78,122` | retype manager/handle |
| `lifecycle/persistence.rs:9,14,26` | retype; `handle.profile.as_str()` → `handle.network.as_str()` (JSON **key** `"profile"` stays — see Format) |
| `service.rs:5,18,29` | `WorkspaceProfileManager` → `WorkspaceManager` |
| `service/support.rs:18` | `workspace_error_from_profile_error` → `workspace_error_from_manager_error`; error type |
| `service/support.rs:30-38` | `active_profile_id` → `active_session_id`; `profile_id` → `session_id` |
| `service/impls/create_workspace.rs:3,27,28,30,38,41` | imports + `profile_snapshot`→`snapshot`, `profile_handle`→`session`, `.enter_with_profile(`→`.open(`, `request.profile`→`request.network`, error-map fn |
| `service/impls/destroy_workspace.rs:3,18,20,23` | `active_session_id`, `session_id`, `.exit(`→`.close(`, error-map fn |
| `service/impls/capture_changes.rs:7,20-29` | **collapse double-lookup**: drop `active_profile_id` call, use `state.manager.handles.get(&handle.id).ok_or(WorkspaceError::NotOpen)?` → `session`; `.dirs.upperdir` |

### model.rs (Tier B field + type refs)

`model.rs:9` import; `WorkspaceHandle.profile`→`network` (:128) + Debug (:139);
`WorkspaceLaunchContext.profile`→`network` (:259) + builders/`From` (:168-265,
:469,483); `CreateWorkspaceRequest.profile`→`network` (:388);
`From<&WorkspaceProfileHandle>`→`From<&MountedWorkspace>` (:464); `WorkspaceProfileFds`→`HolderNsFds` ctor (:176,246,265).

### Path-only updates (`use crate::profile::…` → `use crate::session::…`, types renamed)

`namespace/mod.rs:10` · `namespace/holder.rs:25-26,37,…` ·
`namespace/setns_runner.rs:7-8,23,40,55` (also `isolated_setup::{…}` → `isolated_network_setup::{…}` at :4) ·
`namespace/fds.rs:16,…` · `isolated_network_setup/mod.rs:4-5` ·
`isolated_network_setup/netfilter/mod.rs:4-5` · `isolated_network_setup/rtnl.rs:6`.

### Optional cosmetic (single-use indirection — fold in if convenient)

- `namespace/setns_runner.rs:37` — inline `mount_overlay_via_engine` into `mount_overlay`'s Linux branch.
- `isolated_network_setup/mod.rs:166` — inline `teardown_host_veth` into `teardown_veth`.

---

## Cross-crate (compat) — must land in the same change

Workspace-internal crates (no semver), but renamed symbols/paths are referenced
outside `workspace/src`:

| File:line | Change |
|---|---|
| `operation/src/services.rs:6,34` | `profile::WorkspaceProfileManager` → `session::WorkspaceManager` (import + ctor) |
| `operation/src/observability.rs:16` | `RuntimeWorkspaceSnapshot.profile: NetworkProfile` → `network` (typed field); `snapshot.rs:32` build reads `session.handle.network` (see DB-migration section) |
| `operation/src/command/service/core.rs:131` | `CreateWorkspaceRequest { profile: … }` → `network:` |
| `operation/src/operations/registry/workspace_session_operations.rs` | builds `CreateSessionRequest` → `network:` |
| `operation/tests/*`, `daemon/tests/unit/observability.rs:758` | `WorkspaceHandle`/`CreateWorkspaceRequest` `profile:` literals → `network:` (~50 sites) |
| `workspace/tests/unit/service.rs:9,101` | `session::{ResourceCaps, WorkspaceManager}` + ctor + `CreateWorkspaceRequest.network` |
| `workspace/tests/unit/model.rs:13,27-28,42,…` | `session::{HolderNsFds, MountedWorkspace}` + `.network` literals |

`WorkspaceProfileError`, `enter_with_profile`, `exit`, `active_profile_id` have no
external references — safe.

### Coordinate with `docs/network-profile-refactor`

Tier B here **is** that spec's Option C (DTO/handle field rename). The wire-key
rename (`profile` → `network_profile`) and this field rename (`profile` →
`network`) must land together or be sequenced deliberately — don't rename the wire
key, DTO field, and handle field piecemeal.

---

## Format / out of scope

- **`manager.json` key `"profile"`** (`persistence.rs:26`) → `"network_profile"`.
  This file is **write-only** (no read/restore path in `workspace/src`), so the
  rename is safe with no data migration. Value (`shared`/`isolated`) unchanged.
- **`*_for_test` constructors in `src/model.rs`** — violate "no test code in
  `src/`", but are consumed by *other crates'* tests, so they need a test-support
  seam (feature/crate). Separate effort; left as `#[doc(hidden)] pub` for now.
- **`WorkspaceHandle.base_revision`** duplicates `snapshot` data and is hand-synced
  by `operation` (`workspace_session/service/model.rs:42-44`). Public + cross-crate
  mutated — leave; separate cleanup.
- **`MountedWorkspace` scattered manifest fields** (`manifest_version`/
  `manifest_root_hash`/`base_manifest`/`layer_paths`) could collapse into a held
  `LayerStackSnapshotRef`. Touches persistence; separate.

---

## Names to keep (do not touch)

- `NetworkProfile` / `Shared` / `Isolated` / `as_str()`.
- `NamespaceNetwork`, `NamespacePlan`, `NamespaceFd`.
- `IsolatedNetwork`, `VethAllocation`, `BridgeAddressPool` (types inside the renamed
  `isolated_network_setup/` module).
- `WorkspaceHandle`, `WorkspaceEntry`, `WorkspaceEntryFds`, `WorkspaceSessionId`,
  `ResourceCaps`, `Rfc1918Egress`, `ExitOutcome`.
- Timing/phase names (`phases_ms`, `record_phase_ms`, `spawn_ns_holder`, …).

---

## Observability DB schema migration

Renames the observability surface from `profile` to network-profile vocabulary,
end to end. Spans `sandbox-runtime-operation`, `sandbox-daemon`, and
`sandbox-observability`.

### Naming convention (applied throughout)
- **Typed `NetworkProfile` fields → `network`** (the type already says "profile"):
  `WorkspaceHandle`/`MountedWorkspace`/`CreateWorkspaceRequest` (Tier B) **and**
  `RuntimeWorkspaceSnapshot`.
- **Serialized string surfaces → `network_profile`** (no type to disambiguate;
  matches the wire key from `network-profile-refactor`): the SQL column, the
  `Option<String>` record/row fields, the validation key, and the JSON output key.

### DB migration (append-only — do NOT edit V2)

The store is checksum-guarded; editing applied migrations errors with
`MigrationChecksumMismatch`. Add a new migration, mirroring V6's `RENAME COLUMN`
precedent:

| File:line | Change |
|---|---|
| `sandbox-observability/src/store/schema.rs:11-52` | append `Migration { version: 9, name: "phase_7_workspace_network_profile_rename", sql: V9_SCHEMA_SQL }` |
| `…/schema.rs` (new const) | `V9_SCHEMA_SQL = "ALTER TABLE workspace_snapshots RENAME COLUMN profile TO network_profile;"` |

`V2_SCHEMA_SQL:122` `profile TEXT` stays byte-for-byte (its checksum is locked).

### Code surfaces (column = `network_profile`)

| File:line | Change |
|---|---|
| `sandbox-observability/src/store.rs:131,144,158` | INSERT column, `profile = excluded.profile` upsert, `&snapshot.profile` bind → `network_profile` |
| `sandbox-observability/src/store/read.rs:41,194` | SELECT column + `network_profile: row.get(2)?` |
| `sandbox-observability/src/store/rows.rs:31` | `ObservabilityWorkspaceSnapshotRow.profile` → `network_profile` |
| `sandbox-observability/src/records.rs:68,88` | `WorkspaceSnapshotRecord.profile` → `network_profile`; `validate_optional("profile", …)` → `"network_profile"` |
| `sandbox-daemon/src/observability/service.rs:248` | `profile: Some(bound_kind(workspace.profile.as_str()…))` → `network_profile: Some(bound_kind(workspace.network.as_str()…))` |
| `sandbox-daemon/src/observability/service.rs:478` | JSON output key `"profile": workspace.profile.as_deref()` → `"network_profile": workspace.network_profile.as_deref()` |
| `operation/src/observability.rs:16` | `RuntimeWorkspaceSnapshot.profile` → `network` (typed) |
| `operation/src/workspace_session/service/snapshot.rs:32` | `profile: session.handle.profile` → `network: session.handle.network` |

### Tests

| File:line | Change |
|---|---|
| `sandbox-observability/tests/schema.rs:116` | `assert_eq!(checksums.len(), 8)` → `9` |
| `sandbox-observability/tests/support/mod.rs:152` | `profile: Some("shared")` → `network_profile: Some("shared")` |
| `sandbox-daemon/tests/unit/observability.rs:756-758` | `RuntimeWorkspaceSnapshot { profile: NetworkProfile::… }` → `network:`; any `"profile"` output assertion → `"network_profile"` |

A round-trip read on a pre-V9 DB returns the renamed column (SQLite `RENAME COLUMN`
preserves data), so existing snapshots keep their `shared`/`isolated` values.

---

## Verification
- `cargo fmt`
- `cargo build` (no warnings)
- `cargo clippy --all-targets` (changed crates)
- `cargo test -p sandbox-runtime-workspace -p sandbox-runtime-operation -p sandbox-daemon -p sandbox-observability`
- Migration check: open a DB written at schema v8, confirm it upgrades to v9 and
  `SELECT network_profile FROM workspace_snapshots` returns prior values.
- Grep gate under `crates/`: no `WorkspaceProfile`, `crate::profile::`,
  `isolated_setup`, `enter_with_profile`, `active_profile_id`, or
  `record_create_phase_ms` remain; no bare `profile` identifier/key/column survives
  except inside `NetworkProfile` (type) and `network_profile` (string surfaces).
