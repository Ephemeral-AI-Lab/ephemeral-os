# Cross-repo contract: protocol version + fixture pin

The Rust `eosd` runtime pins two version surfaces that must move deliberately.
A careless bump silently breaks the thin-client handshake or the on-disk
manifest read path.

## 1. Wire protocol version

- `DAEMON_PROTOCOL_VERSION = 1`
- Carried as the `_eos_daemon_protocol_version` field **inside `args`** on every
  request. The daemon does **not** gate on it today (inert hook); it is present
  so a future version can branch.
- Source of truth: `sandbox/crates/eos-protocol/src/version.rs`
  (`DAEMON_PROTOCOL_VERSION`). Host-side daemon clients **derive** their copy from
  that crate via a unilateral path dependency into `sandbox/crates/eos-protocol`,
  so host↔daemon protocol lockstep is compiler-enforced on the Rust side rather
  than hand-maintained.

## 2. On-disk manifest schema version

- `MANIFEST_SCHEMA_VERSION = 1`
- Stamped into the persisted layer-stack manifest. The CAS `manifest_root_hash`
  hashes **only** the `layers` array, never `version`/`schema_version`, so the
  schema version can change without invalidating existing layer hashes — but a
  reader that does not understand a new schema version must refuse to load it.

## 3. Bump procedure

When either version must change:

1. Bump the constant in the owning Rust crate and update this file in the same
   change.
2. The golden fixtures (`crates/eos-protocol/fixtures/`) are **immutable ground
   truth** captured from the original Python runtime, which has been removed —
   they can no longer be regenerated. Never edit a fixture to match Rust code.

Until such a change, both versions are pinned at `1`.
