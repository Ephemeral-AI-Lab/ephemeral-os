# Cross-repo contract: protocol version + fixture pin

The Rust `eosd` runtime and the Python backend share two version surfaces that
must be bumped as **coordinated cross-repo events**. Neither may move on one side
alone — a unilateral bump silently breaks the thin-client handshake or the
on-disk manifest read path.

## 1. Wire protocol version

- `DAEMON_PROTOCOL_VERSION = 1`
- Carried as the `_eos_daemon_protocol_version` field **inside `args`** on every
  request. The daemon does **not** gate on it today (inert hook); it is present
  so a future version can branch.
- Source of truth (Python): `backend/src/sandbox/host/daemon_client.py:46-47`.

## 2. On-disk manifest schema version

- `MANIFEST_SCHEMA_VERSION = 1`
- Stamped into the persisted layer-stack manifest. The CAS `manifest_root_hash`
  hashes **only** the `layers` array, never `version`/`schema_version`, so the
  schema version can change without invalidating existing layer hashes — but a
  reader that does not understand a new schema version must refuse to load it.

## 3. Coordinated bump procedure

When either version must change:

1. Land the Python change and the Rust change in lockstep (same logical release).
2. Regenerate the golden fixtures from the live Python
   (`crates/eos-protocol/fixtures/`) only as part of the same coordinated bump.
   Fixtures are otherwise **immutable ground truth** — never edit a fixture to
   match Rust code.
3. Bump the constant on both sides and update this file.

Until such a coordinated event, both versions are pinned at `1`.
