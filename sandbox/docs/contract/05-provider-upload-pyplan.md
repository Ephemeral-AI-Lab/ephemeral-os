# 05 — Provider Upload: Phase 0 Python-Side Change Plan

**Status:** SOURCE-OF-TRUTH SPEC for the later implementing agent. This is a
*plan*, not an implementation. It is a READ-only pass over `backend/`; every
anchor below was opened and confirmed against the live tree (commit at session
time). Cited as `path:line`.

**Scope (Phase 0 only).** The migration plan (`docs/plans/sandbox-rust-external-migration-PLAN.md`)
§6 Phase 0 line 197 states: *"Replaces: nothing yet; adds the `put_archive`
primitive alongside `chunked_upload.py`."* Therefore Phase 0 is:

1. Add a `put_archive` primitive to the `ProviderAdapter` Protocol + Docker
   adapter impl; Daytona stub.
2. Add the `backend/src/sandbox/host/runtime_artifact/` tiny data module (pin
   scaffold: version, per-arch SHA256, minisign pubkey, protocol_version).
3. Add the `backend/src/sandbox/_contract_fixtures/` vendored-fixtures scaffold
   + the dual-CI pin-assert design.
4. The exact pytest targets to run after the change.

**Explicitly OUT of Phase 0** (later phases — do NOT touch in this change):
- Any edit to `runtime_bundle.py` (the base64-over-exec upload + `tar -xzf`
  finalize stays as-is; `put_archive` is *added alongside*, not wired in yet).
- The `EOS_SANDBOX_RUNTIME` dispatch fork in `daemon_client.py` (Phase 2).
- Host-side minisign signature-verify *logic* (Phase 0 only records the trust
  anchor data; the verify code is a later phase, AV-8).
- The AF_UNIX local-fallback connector, the `_PYTHON_CANDIDATES` probe drop,
  PPC plugin protocol, isolated-workspace lifecycle changes.

---

## 0. Verified ground truth (anchors confirmed this session)

| Fact | Anchor | Confirmed value |
| --- | --- | --- |
| `ProviderAdapter` Protocol surface — exec + CRUD + preview + context_preparer; NO upload primitive today | `backend/src/sandbox/provider/protocol.py:21-79` | `exec` is the only I/O-stream method; no `put_archive` |
| Docker adapter has no upload method; `exec` wraps `container.exec_run` via `asyncio.to_thread` | `backend/src/sandbox/provider/docker/adapter.py:337-383` | async exec pattern to mirror |
| Docker `create()` path (context) | `backend/src/sandbox/provider/docker/adapter.py:141-205` | builds container, `host_config_kwargs()`, starts it |
| `get_async_docker_client()` returns the *sync* client; adapter wraps blocking calls in `asyncio.to_thread` | `backend/src/sandbox/provider/docker/client.py:131-138` | "docker-py has no first-class async API" |
| docker-py installed version | `uv run python -c 'import docker; print(docker.__version__)'` | **7.1.0** |
| docker-py dependency pin (optional `docker` extra) | `pyproject.toml:36-37` (repo ROOT, not `backend/`) | `docker>=7.0.0` |
| `Container.put_archive(self, path, data)` signature + semantics | docker-py 7.1.0 `docker.models.containers.Container.put_archive` (via `inspect`) | `path` (dest dir, **must already exist**), `data` (bytes or stream of tar), returns `bool`; raises `docker.errors.APIError` |
| Daytona adapter — has no `put_archive`; out of scope | `backend/src/sandbox/provider/daytona/adapter.py:107-352` | must still satisfy the Protocol |
| Current upload finalize: in-image `tar -xzf` + base64 | `backend/src/sandbox/host/runtime_bundle.py:355-360` | the `base64 -d`/`tar -xzf` path `put_archive` eventually replaces |
| Current in-image dest mkdir | `backend/src/sandbox/host/runtime_bundle.py:335` | `mkdir -p {BUNDLE_REMOTE_DIR}` |
| `DEFAULT_CHUNK_SIZE` | `backend/src/sandbox/host/chunked_upload.py:10` | `32 * 1024` |
| Daemon launch path (where a future `EOS_SANDBOX_RUNTIME` read goes; no-op Phase 0) | `backend/src/sandbox/host/daemon_client.py:606-635` (`_daemon_spawn_command`) | launches `sh launch_daemon.sh ... sandbox.daemon` |
| `DAEMON_PROTOCOL_VERSION` | `backend/src/sandbox/host/daemon_client.py:46` | `1` |
| `EOS_SANDBOX_RUNTIME`, `minisign`, `runtime_artifact`, `_contract_fixtures` | grep over `backend/src` + `backend/tests` | **do NOT exist yet — all net-new** |
| Protocol-conformance gate uses a hand-maintained method tuple | `backend/tests/unit_test/test_sandbox/test_provider/test_protocol_conformance.py:8-22` | `_PROTOCOL_METHODS` does NOT auto-sync with the Protocol |
| `/sandbox/crates/eos-protocol/fixtures/` | `ls` | **does not exist yet** (`/sandbox` is empty) |

**docker-py version risk — CLOSED, not open.** `put_archive(path, data)` has been
on `Container` since docker-py ~2.0; the `>=7.0.0` floor and the installed
7.1.0 both provide it. No version bump needed. (Recorded here so the later
agent does not re-investigate.)

---

## 1. `put_archive` — Protocol addition + Docker adapter impl + Daytona stub

### 1.1 The streaming primitive (the criterion-4 win)

The whole point of this primitive: **dockerd unpacks the tar stream itself**
(the Docker Engine `PUT /containers/{id}/archive` endpoint). Nothing spawns
`tar`/`gzip`/`base64` *inside* the container. This is the criterion-4 lever vs
the current `runtime_bundle.py:355-360` finalize (`base64 -d >> staging` per
chunk, then in-image `tar -xzf`). One streamed transfer, constant w.r.t. blob
size (CP-1).

**dest_dir must already exist.** docker-py `put_archive(path, data)` requires
`path` to be an existing directory in the container; the engine extracts the
tar *into* it. Phase 0 callers (and the Phase 2 eosd upload caller) must ensure
the dest exists first. Shell-free dest-dir creation via tar parent entries is
the **Phase 5 / M5** concern (plan line 156), NOT Phase 0. The primitive does
not create the dest.

**Uncompressed tar; mode set in the tarinfo by the CALLER.** A stripped binary
gains nothing from gzip and avoids relying on engine-side decompression. The
primitive is intentionally generic: it takes an opaque `tar_stream` + a
`dest_dir`. It does NOT bake eosd-specific `chmod +x`/mode logic in — the
later Phase 2 caller builds the tar with the executable bit (mode `0o755`) in
the `TarInfo`. Keeping the primitive payload-agnostic is what keeps Phase 0
surgical.

### 1.2 Protocol addition — `backend/src/sandbox/provider/protocol.py`

Add a new method to the `ProviderAdapter` Protocol, placed in a new
`# -- Upload -------` section between the `Exec` block (ends `:64`) and the
`Context preparation` block (`:66`). Async, matching the `exec` analog (per-call
I/O; a multi-MB blocking upload must not run on the event loop):

```python
    # -- Upload --------------------------------------------------------------

    async def put_archive(
        self,
        sandbox_id: str,
        *,
        tar_stream: bytes,
        dest_dir: str,
    ) -> None:
        """Stream a tar archive into *dest_dir* inside the sandbox.

        The provider unpacks the tar server-side — no in-image
        ``tar``/``gzip``/``base64`` is spawned. *dest_dir* must already exist;
        the archive is extracted into it. Tar entry modes (e.g. the executable
        bit) are set by the caller in the archive, not here.
        """
        ...
```

- Signature note vs the task brief: the task names `put_archive(sandbox_id, *, tar_stream, dest_dir)`.
  This spec keeps those exact params and adds `async def`. See Risks for why
  the `async` deviation is correct and intentional.
- `tar_stream` typed `bytes`: the host already materializes the whole bundle in
  memory today (`runtime_bundle.py:_runtime_bundle_bytes` returns `bytes`); a
  `bytes` payload matches that reality and matches docker-py's accepted `data`
  type. (docker-py also accepts a file-like stream; `bytes` is the smaller,
  honest contract for Phase 0. If a streamed source is later wanted, widen to
  `bytes | IO[bytes]` then — do not speculate now.)

### 1.3 Docker adapter impl — `backend/src/sandbox/provider/docker/adapter.py`

Add the method in a new `# -- Upload -------` section, placed after the `exec`
method (ends `:383`) and before `# -- Context preparation` (`:385`). Mirror the
exact async-over-sync shape of `exec` (`adapter.py:337-383`):

```python
    # -- Upload --------------------------------------------------------------

    async def put_archive(
        self,
        sandbox_id: str,
        *,
        tar_stream: bytes,
        dest_dir: str,
    ) -> None:
        client = await self._get_async_client()

        def _run() -> None:
            container = client.containers.get(sandbox_id)
            ok = container.put_archive(path=dest_dir, data=tar_stream)
            if not ok:
                raise RuntimeError(
                    f"docker put_archive returned False "
                    f"(sandbox={sandbox_id!r}, dest_dir={dest_dir!r})"
                )

        await asyncio.to_thread(_run)
```

Rationale for each line:
- `self._get_async_client()` — same lazy-client accessor `exec` uses
  (`adapter.py:345`); `get_async_docker_client()` returns the sync client
  (`client.py:131-138`), so the blocking SDK call must run in a worker thread.
- `container.put_archive(path=dest_dir, data=tar_stream)` — the docker-py
  call confirmed in §0; `path` is the (pre-existing) dest dir, `data` is the
  raw tar bytes. The engine unpacks server-side.
- `if not ok: raise` — docker-py returns `bool`; `False`/`APIError` are the
  only failure surfaces. Raising on `False` keeps the primitive fail-loud,
  consistent with the adapter's other raise-on-failure paths
  (e.g. `create` re-raises non-ImageNotFound, `adapter.py:198`). Do NOT add a
  timeout param in Phase 0 — `exec` only timeouts because the in-container
  command can hang; a server-side archive PUT is bounded by the HTTP client and
  has no analog need. (If CP-1 measurement later wants an upload timeout, add it
  then.)

No new imports needed: `asyncio` is already imported (`adapter.py:9`).

### 1.4 Daytona stub — `backend/src/sandbox/provider/daytona/adapter.py`

Daytona is out of scope (plan §0 "Docker only. Daytona out of scope."), but
`ProviderAdapter` is a structural `Protocol`, so `DaytonaProviderAdapter` must
still expose the method or the conformance gate (§4) fails. Add a fail-loud
stub in a new `# -- Upload -------` section, after `exec` (ends `:339`) and
before `# -- Hook -------` (`:341`):

```python
    # -- Upload --------------------------------------------------------------

    async def put_archive(
        self,
        sandbox_id: str,
        *,
        tar_stream: bytes,
        dest_dir: str,
    ) -> None:
        raise NotImplementedError(
            "DaytonaProviderAdapter.put_archive is not implemented; "
            "the eosd binary-upload path is Docker-only (migration plan §0)."
        )
```

This satisfies the Protocol structurally and fails loud if any Daytona caller
ever reaches it. No new imports needed.

### 1.5 Implementor enumeration — exactly TWO adapters need the method

Confirmed this session: the ONLY in-repo `ProviderAdapter` implementors are
`DockerProviderAdapter` and `DaytonaProviderAdapter`. There is no third adapter
class or test-double implementing the Protocol:
- `tests/unit_test/test_engine/_fake_provider.py` (`FakeProviderClient`) is an
  ENGINE provider implementing `SupportsStreamingMessages` (`stream_message`),
  NOT `ProviderAdapter` — it has no `exec`/`create`/etc. No stub needed.
- The other `ProviderAdapter` grep hits are `protocol.py` (the Protocol),
  `registry.py`/`bootstrap.py`/`context_preparer.py` (consumers, not
  implementors), and the provider tests that reference the existing two
  adapters. None defines a new implementor.

`protocol.py:21` is a plain `Protocol` (NOT `@runtime_checkable`), so nothing
does an `isinstance`/structural runtime check that adding a method would break.
The impact of the new Protocol method is purely mypy/completeness across the two
named adapters — both are covered by §1.3 + §1.4.

---

## 2. `runtime_artifact/` — tiny data module

**Path:** `backend/src/sandbox/host/runtime_artifact/__init__.py` (NEW package).

Follow the task's explicit `host/runtime_artifact/` placement. (Plan §1 line 107
says `backend/src/sandbox/runtime_artifact/`; §11 line 345 says
`backend/src/sandbox/host/runtime_artifact/`. The two disagree — see Risks. This
spec follows the task + §11.)

Keep it a **tiny pure-data module** — no logic, no I/O, no verify code (the
host signature-verify path is a later phase, AV-8). It is the entire
consumer-side coupling surface: the pinned `eosd` version, per-arch SHA256, the
minisign trust-anchor public key, and the protocol version the pinned binary
speaks. Phase 0 records placeholders to be filled by the first signed `eosd`
release; `protocol_version` is real today (`1`).

Proposed contents:

```python
"""Pinned ``eosd`` runtime-artifact coupling surface (consumer side).

The ENTIRE coupling between the Python host and the external ``/sandbox`` Rust
runtime is: the wire protocol, the data-type contract (see
``sandbox/_contract_fixtures/``), and THIS pin. The host fetches + verifies the
released ``eosd-linux-{arch}`` binary against the SHA256 + minisign signature
recorded here before any upload/exec (verify logic lands in a later phase —
this module is data only).

Phase 0 scaffold: SHA256s + pubkey are placeholders until the first signed
release. ``PROTOCOL_VERSION`` is already real (mirrors
``host.daemon_client.DAEMON_PROTOCOL_VERSION``).
"""

from __future__ import annotations

# Released eosd artifact this backend is pinned to. Bumped on a coordinated
# release per CONTRACT.md. "" until the first release exists.
EOSD_VERSION = ""

# Per-arch SHA256 of the released binary. Keys = container arch tokens the host
# maps to (amd64 / arm64). "" until the first release exists.
EOSD_SHA256: dict[str, str] = {
    "amd64": "",
    "arm64": "",
}

# Minisign trust-anchor public key (the release signing key). The host verifies
# each binary's detached .minisig against THIS key (fail-closed, AV-8). "" until
# the signing key is provisioned.
MINISIGN_PUBLIC_KEY = ""

# Wire protocol version the pinned eosd speaks. MUST stay in lockstep with
# host.daemon_client.DAEMON_PROTOCOL_VERSION; a bump is a coordinated release
# event (CONTRACT.md).
PROTOCOL_VERSION = 1

__all__ = [
    "EOSD_VERSION",
    "EOSD_SHA256",
    "MINISIGN_PUBLIC_KEY",
    "PROTOCOL_VERSION",
]
```

Notes for the implementer:
- Arch keys (`amd64`/`arm64`) match the released artifact names
  `eosd-linux-{amd64,arm64}` (plan §1 line 103) and the host's
  container-arch selection (plan §2 line 120). The probed-arch →
  key mapping (`uname -m` → `x86_64`→`amd64`, `aarch64`→`arm64`) is the host's
  SELECTION logic, a later phase — this module is data only and must not be
  mistaken for the selection code.
- A focused Phase 0 test is OPTIONAL but cheap: assert
  `runtime_artifact.PROTOCOL_VERSION == daemon_client.DAEMON_PROTOCOL_VERSION`
  to lock the lockstep invariant. Not strictly required by the task; mention as
  a follow-up if the implementer wants a guard.
- Do NOT add the `eosd-linux-*` binaries to the repo in Phase 0; they are
  "fetched+verified at deploy" (plan §11 line 347).

---

## 3. `_contract_fixtures/` — vendored pinned fixtures + dual-CI pin-assert

**Path:** `backend/src/sandbox/_contract_fixtures/` (NEW). Follow the task + plan
§11 line 349. (Plan §2 line 116 instead says
`backend/tests/.../sandbox_protocol_fixtures/` — the two disagree; see Risks.
Follow the task.)

### 3.1 What it is

A vendored, pinned copy of the canonical `/sandbox/crates/eos-protocol/fixtures/`
golden request/response JSON set, plus a manifest recording the upstream
commit/tag and a content hash. The canonical source of truth is the Rust
`eos-protocol` crate (plan §1 line 68, §2 line 116); the backend vendors a
pinned copy so the Python protocol layer can diff its emitted envelopes against
the same goldens the Rust side asserts. (Consuming these fixtures in real
protocol tests is a later phase; Phase 0 lays the scaffold + the pin-assert.)

### 3.2 Phase 0 chicken-and-egg state — DOCUMENT IT

`/sandbox/crates/eos-protocol/fixtures/` **does not exist yet** (`/sandbox` is
empty this session). So in Phase 0 there is no upstream to hash against. The
honest Phase 0 state:
- Create `backend/src/sandbox/_contract_fixtures/` with a `MANIFEST.json`
  (or `__init__.py` + a `pin.json`) recording: `upstream_repo`,
  `upstream_commit` (empty/`"UNPINNED"` until the first frozen set),
  `sha256` of the vendored tree (empty until fixtures land).
- The dual-CI pin-assert test is checked in but **skips/xfails while
  `upstream_commit` is unpinned**, flipping to a hard assert once the first
  frozen fixture set is published. State this transition explicitly so the
  later agent does not interpret an empty pin as a green assert.

Proposed `backend/src/sandbox/_contract_fixtures/pin.json` (Phase 0 scaffold):

```json
{
  "upstream_repo": "sandbox/crates/eos-protocol/fixtures",
  "upstream_commit": "UNPINNED",
  "vendored_sha256": "",
  "note": "Phase 0 scaffold. Filled when the first eos-protocol fixture set is frozen; until then the dual-CI pin-assert skips."
}
```

### 3.3 The dual-CI pin-assert design

The drift-mitigation (plan §2 line 116, OQ#3 resolved): **both** CIs assert the
pin so a fixture drift fails both pipelines, never neither.
- **Rust CI** (in `/sandbox`, later): asserts the canonical
  `eos-protocol/fixtures` match the envelopes `eosd` actually emits.
- **Python CI** (here): asserts the **vendored copy's content hash equals the
  pinned upstream hash** recorded in `pin.json`. If someone edits a vendored
  fixture without re-pinning (or upstream changes without re-vendoring), the
  hashes diverge and Python CI fails.

Proposed Python-side assert (checked in as a test; fenced here, NOT written
under `backend/` in this read pass):

```python
# backend/tests/unit_test/test_sandbox/test_contract_fixtures_pin.py
"""Dual-CI pin assert: vendored fixture hash == pinned upstream hash.

Phase 0: skips while the upstream fixture set is UNPINNED. Once
eos-protocol/fixtures is frozen and the backend vendors + pins a copy, this
flips to a hard assert so any vendored/upstream drift fails Python CI.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

_FIXTURES_PKG = Path(__file__).resolve().parents[3] / "src" / "sandbox" / "_contract_fixtures"


def _compute_vendored_sha256(root: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*.json") if p.name != "pin.json"):
        h.update(path.relative_to(root).as_posix().encode())
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def test_vendored_fixtures_match_pinned_hash() -> None:
    pin = json.loads((_FIXTURES_PKG / "pin.json").read_text())
    if pin.get("upstream_commit") in ("", "UNPINNED"):
        pytest.skip("eos-protocol fixtures not frozen/pinned yet (Phase 0 scaffold)")
    expected = pin["vendored_sha256"]
    actual = _compute_vendored_sha256(_FIXTURES_PKG)
    assert actual == expected, (
        "Vendored eos-protocol fixtures drifted from the pinned hash. "
        "Re-vendor + re-pin (CONTRACT.md), or revert the fixture edit."
    )
```

- The hash function (sorted relative path + null-separated bytes) is a
  *proposal*; the binding requirement is only that the **same canonicalization
  is used on both sides** so the two CIs compare equal hashes. The Rust-side
  hashing must match this scheme (record it in `CONTRACT.md`). Flag in Risks.
- `parents[3]` resolves
  `backend/tests/unit_test/test_sandbox/` → `backend/`; verify the depth when
  the test file is actually placed (path math is a write-phase detail).

---

## 4. Exact pytest targets to run after the change

Run from the **repo root** `/Users/yifanxu/machine_learning/LoVC/EphemeralOS`
(this is where `pyproject.toml` and `uv.lock` live — NOT `backend/`):

```
uv run pytest \
  backend/tests/unit_test/test_sandbox/test_provider/test_docker_adapter.py \
  backend/tests/unit_test/test_sandbox/test_provider/test_protocol_conformance.py
```

**Green baseline captured this session (BEFORE any change):** `24 passed in 0.12s`.

After the change these MUST still pass, plus:

1. **`test_protocol_conformance.py` — add `"put_archive"` to `_PROTOCOL_METHODS`**
   (`test_protocol_conformance.py:8-22`). This tuple is **hand-maintained and
   does NOT auto-sync** with the Protocol — it is the real discriminating gate
   for "both adapters expose the method." Miss this list edit and the test
   silently fails to guard the new method. Both
   `test_daytona_adapter_implements_protocol` and
   `test_docker_adapter_implements_protocol` then assert the method exists on
   both adapters (Daytona via its `NotImplementedError` stub, §1.4).

2. **`test_docker_adapter.py` — add a focused `test_put_archive`** (in-scope for
   a diff plan). Proposed (mirrors the existing `test_exec_*` style at
   `test_docker_adapter.py:303-338`, fenced here — NOT written under
   `backend/` in this read pass):

   ```python
   def test_put_archive_streams_tar_to_dest(
       adapter: DockerProviderAdapter, fake_client: MagicMock
   ) -> None:
       container = fake_client.containers.get.return_value
       container.put_archive.return_value = True

       asyncio.run(
           adapter.put_archive("c-1", tar_stream=b"TARBYTES", dest_dir="/opt/eos")
       )

       container.put_archive.assert_called_once_with(
           path="/opt/eos", data=b"TARBYTES"
       )


   def test_put_archive_raises_on_false(
       adapter: DockerProviderAdapter, fake_client: MagicMock
   ) -> None:
       container = fake_client.containers.get.return_value
       container.put_archive.return_value = False

       with pytest.raises(RuntimeError, match="put_archive returned False"):
           asyncio.run(
               adapter.put_archive("c-1", tar_stream=b"x", dest_dir="/opt/eos")
           )
   ```

3. **`test_contract_fixtures_pin.py`** (new, §3.3) — runs and `skip`s in Phase 0.

Optional broader sweep (no behavior change expected, cheap): the whole
provider suite directory:
```
uv run pytest backend/tests/unit_test/test_sandbox/test_provider/
```

---

## 5. Exact file change list

**Edit:**
- `backend/src/sandbox/provider/protocol.py` — add async `put_archive` to the
  `ProviderAdapter` Protocol (§1.2).
- `backend/src/sandbox/provider/docker/adapter.py` — add async `put_archive`
  impl wrapping `container.put_archive(path=..., data=...)` via
  `asyncio.to_thread` (§1.3).
- `backend/src/sandbox/provider/daytona/adapter.py` — add async `put_archive`
  `NotImplementedError` stub (§1.4).
- `backend/tests/unit_test/test_sandbox/test_provider/test_protocol_conformance.py`
  — add `"put_archive"` to `_PROTOCOL_METHODS` (§4.1).
- `backend/tests/unit_test/test_sandbox/test_provider/test_docker_adapter.py`
  — add `test_put_archive_*` (§4.2).

**Add (new):**
- `backend/src/sandbox/host/runtime_artifact/__init__.py` — tiny pin data
  module (§2).
- `backend/src/sandbox/_contract_fixtures/pin.json` — Phase 0 pin scaffold (§3.2).
  (Plus an `__init__.py` if the dir must be an importable package; the proposed
  test reads via filesystem path, so an `__init__.py` is optional. The
  `fixtures/` JSON files themselves arrive when upstream is frozen — later
  phase.)
- `backend/tests/unit_test/test_sandbox/test_contract_fixtures_pin.py` —
  dual-CI Python-side pin-assert (skips in Phase 0) (§3.3).

**Context only — do NOT edit in Phase 0:**
- `backend/src/sandbox/host/runtime_bundle.py` (current finalize `:355-360`,
  mkdir `:335`) — stays on the base64-over-exec path; `put_archive` is added
  alongside, wired in later (plan line 197).
- `backend/src/sandbox/host/daemon_client.py` — launch path is
  `_daemon_spawn_command` (`:606-635`); a future `EOS_SANDBOX_RUNTIME` read
  would gate which launch/connect path runs there. **No-op in Phase 0** (the
  flag + fork are Phase 2). No edit this phase.
- `backend/src/sandbox/host/chunked_upload.py` (`DEFAULT_CHUNK_SIZE = 32*1024`,
  `:10`) — unchanged.

---

## 6. Risks / ambiguities

1. **`async def put_archive` deviates from the task's literal signature.** The
   task wrote `put_archive(sandbox_id, *, tar_stream, dest_dir)` without
   `async`. This spec makes it `async def` because (a) the only realistic caller
   is the async upload path, (b) a multi-MB blocking upload on the event loop is
   wrong, and (c) `exec` — the exact I/O analog — is already
   `async def` + `asyncio.to_thread`. Intentional, justified deviation; confirm
   if a sync primitive was actually wanted.

2. **`runtime_artifact/` path conflict in the plan.** §1 line 107 says
   `backend/src/sandbox/runtime_artifact/`; §11 line 345 says
   `backend/src/sandbox/host/runtime_artifact/`. This spec follows the task +
   §11 (`host/`). The implementer should pick one and make the plan consistent.

3. **`_contract_fixtures/` path conflict in the plan.** §2 line 116 says
   `backend/tests/.../sandbox_protocol_fixtures/`; §11 line 349 + the task say
   `backend/src/sandbox/_contract_fixtures/`. This spec follows the task + §11
   (`src/sandbox/_contract_fixtures/`). Resolve the plan inconsistency.

4. **Dual-CI pin-assert is a scaffold until upstream fixtures exist.**
   `/sandbox/crates/eos-protocol/fixtures/` does not exist yet, so there is no
   upstream hash to pin against in Phase 0. The Python-side assert skips while
   `upstream_commit == "UNPINNED"` and flips to a hard assert once the first
   frozen set lands. The fixture-tree hash canonicalization MUST be identical on
   the Rust and Python sides (record in `CONTRACT.md`); the hashing scheme in
   §3.3 is a proposal, not yet a contract.

5. **`tar_stream: bytes` vs streamed source.** docker-py accepts `bytes` or a
   file-like stream for `data`. This spec types it `bytes` to match how the host
   already materializes the bundle (`_runtime_bundle_bytes -> bytes`). If a
   later phase needs a true stream (very large payloads), widen to
   `bytes | IO[bytes]` then — do not speculate now.

6. **`put_archive` requires `dest_dir` to pre-exist.** The primitive does NOT
   create the dest. Phase 0/Phase 2 callers ensure existence; shell-free
   dest-creation-via-tar-parent-entries is the Phase 5/M5 concern (plan line
   156). If a caller passes a nonexistent dest, docker-py raises `APIError` —
   acceptable fail-loud behavior for Phase 0.

7. **`pin.json` path-depth math (`parents[3]`)** in the proposed test is
   approximate; verify when the test file is physically placed. Write-phase
   detail, not a contract risk.

**Resolved (not open):** docker-py version / `put_archive` availability —
7.1.0 installed, pin `docker>=7.0.0`, method present since docker-py ~2.0. No
action needed.
