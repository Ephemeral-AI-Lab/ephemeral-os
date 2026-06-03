# Rust parity audit — Provider / provisioning / network namespace

Area: sandbox — provider selection, provisioning/lifecycle, ns-holder / setns / fresh-ns.
Date: 2026-06-03. Auditor: workflow-subagent.

## Ground truth

Authoritative Python (current HEAD, in-tree):
- `backend/src/sandbox/provider/bootstrap.py` — first-call-wins provider dispatcher.
- `backend/src/sandbox/provider/registry.py` — default + per-sandbox adapter registry (WR-01 uncached fallback).
- `backend/src/sandbox/provider/protocol.py` — `ProviderAdapter` Protocol.
- `backend/src/sandbox/provider/docker/adapter.py` + `docker/client.py` — Docker adapter + `host_config_kwargs` caps/tmpfs.
- `backend/src/sandbox/provider/daytona/adapter.py` + `daytona/bootstrap.py` — Daytona adapter.
- `backend/src/config/sections/sandbox.py` — `SandboxConfig` (`default_provider: Literal["docker","daytona"]`).
- `backend/src/runtime/sandbox_provisioning.py` — `RequestSandboxProvisioner.prepare_for_run`.

Authoritative Python for netns (REMOVED FROM HEAD in commit `37c13f3db` "remove legacy
python sandbox runtime subsystems"; recovered from `37c13f3db~1` for this audit — see Open
Questions #1):
- `.../isolated_workspace/scripts/ns_holder.py` — pidns PID-1 holder, 2-step pipe handshake.
- `.../isolated_workspace/scripts/setns_exec.py`, `_setns_libc.py`, `setns_overlay_mount.py`,
  `configure_dns_in_ns.py` — setns helpers + CLONE_* constants.
- `.../isolated_workspace/network.py` — bridge/veth/nft + `BridgeAddressPool`.
- `.../isolated_workspace/_control_plane/namespace_runtime.py` — daemon-side `spawn_ns_holder`,
  `open_ns_fds`, `signal_net_ready` (writes bare `b"net-ready\n"`), `mount_overlay`, `configure_dns`.
- `.../overlay/namespace_runner.py`, `overlay/kernel_mount.py`, `overlay/namespace_entrypoint.py`
  — fresh-ns `unshare -Urm` + overlay mount.

Docs: `docs/architecture/sandbox/provider.html` (§7.1/7.2, `data-last-reviewed-commit=2a3e6cc7c`)
corroborates: one process-wide default + per-sandbox bindings, uncached fallback, `EOS_SANDBOX_PROVIDER`
or central config selects Docker/Daytona, post-lifecycle setup ordering. `space-model.html` and
`agent_loops/provider-sandbox-bridge.html` describe the daemon TCP / AF_UNIX bridge.

## Rust mapping

- Provider seam / types: `agent-core/crates/eos-sandbox-host/src/provider.rs`.
- Provider selection + registry: `agent-core/crates/eos-sandbox-host/src/registry.rs`
  (`resolve_provider_kind`, `ProviderRegistry`).
- Docker adapter: `agent-core/crates/eos-sandbox-host/src/docker.rs`.
- Lifecycle / post-setup: `agent-core/crates/eos-sandbox-host/src/lifecycle.rs`.
- Provisioning: `agent-core/crates/eos-sandbox-host/src/provisioning.rs`.
- Config: `agent-core/crates/eos-config/src/sandbox.rs` (`SandboxProvider`, `DockerConfig`).
- ns-holder: `sandbox/crates/eos-ns-holder/src/lib.rs`.
- bridge/veth/nft: `sandbox/crates/eos-isolated/src/network.rs`.
- daemon-side handshake: `sandbox/crates/eos-daemon/src/isolated.rs` (`signal_net_ready`, etc.).
- runner setns/fresh-ns/mount: `sandbox/crates/eos-runner/src/{setns,fresh_ns,mount}.rs`.

The Python in-process daemon + `unshare(1)`/`python -m ...scripts.X` subprocess model is replaced by
the `eosd` binary that hosts `eos-ns-holder::run` and the `eos-runner` setns/fresh-ns helpers. This is
an INTENTIONAL migration change, not a missing dynamic.

## Invariant table

| # | invariant | status | severity | python file:line | rust file:line | note |
|---|-----------|--------|----------|------------------|----------------|------|
| 1a | Docker default; `EOS_SANDBOX_PROVIDER`/config selects provider | partial | low | bootstrap.py:24-30; sandbox.py:41 | registry.rs:27-43; sandbox.rs:14-18 | Rust is Docker-only; `"daytona"` is REJECTED both at config deserialize (sandbox.rs:108-114) and at `resolve_provider_kind` (registry.rs:38-41). Python accepts both. Divergent by design. |
| 1b | provider bootstrap is process-global, first-call-wins | divergent | low | bootstrap.py:17-18,40-53 (sentinel + 2nd-call warn) | registry.rs:53-83 (`set_default` idempotent overwrite) | Rust drops the first-call-wins sentinel; `set_default` is a last-write-wins overwrite seeded once at the composition root. No "called twice with different provider" warning. Acceptable for single-seed app state but NOT literally first-call-wins. |
| 2a | provisioning: explicit-id starts; else create fresh `request-<8hex>` labelled `origin=workflow,request_id` | match | none | sandbox_provisioning.py:50-79 | provisioning.rs:31-87 | name `request-<8hex>` + labels match; explicit id trimmed; whitespace-only → create branch. `create returned no id` branch eliminated by `SandboxId` non-empty type. |
| 2b | lifecycle create/start/stop/delete/set_labels/ensure_running | match | none | docker/adapter.py:141-276; host/lifecycle.py (per docs 300-322) | lifecycle.rs:62-146; docker.rs:115-302 | CRUD + `ensure_running` probe(`pwd`)→restart→re-setup ported. `delete` disposes registry binding; plugin-cache cleanup intentionally dropped (GC-03, lifecycle.rs:96-99). |
| 2c | post-lifecycle setup order: bg eosd upload ∥ ensure_git → drain → authoritative upload → ensure_workspace_base + readiness gate | match | none | host/bootstrap.py:300-312 (per docs) | lifecycle.rs:150-309 | order preserved; readiness gate `ready==true ∧ control_plane ok ∧ manifest_version>=1` (lifecycle.rs:364-384). |
| 2d | Docker caps/tmpfs/init host-config | match | high | docker/client.py:25-42,72-112 | docker.rs:501-530 | `SYS_ADMIN`+`NET_ADMIN`, `seccomp=unconfined`+`apparmor=unconfined`, `init=true`, tmpfs `/eos` = `rw,exec,size=2g,mode=1777`. Constants byte-identical. |
| 2e | Docker daemon-TCP env/labels + endpoint | match | medium | docker/adapter.py:28-34,170-183,289-339 | docker.rs:37-43,147-168,319-327,643-686 | internal port `37657`, labels/env names, `host_port=None`→random ephemeral, `0.0.0.0`/`::`→`127.0.0.1` normalization all match. |
| 3a | ns-holder keeps netns alive across ops (pins ns FDs, pauses until SIGTERM) | match | high | ns_holder.py:89-115; namespace_runtime.py:79-125 | ns-holder/lib.rs:807-917 | Rust holder `unshare(NEWUSER|NEWNS|NEWPID|NEWNET)`, pins `/proc/self/ns/*` in `HeldNamespaces` (RAII), `pause()` until SIGTERM. Daemon opens `/proc/<pid>/ns/*`. |
| 3b | 2-step pipe handshake: `ns-up` → (`net-ready` prefix) → `ready` | match | medium | ns_holder.py:94,100-111; namespace_runtime.py:205 | ns-holder/lib.rs:256-308; isolated.rs:293-321 | `net-ready` is a `startswith`/`starts_with` PREFIX check both sides. EOF→exit 1, wrong token→exit 2 (lib.rs:131-137). |
| 3c | setns order user→mnt→pid→net; PID before fork; CLONE_* constants | match | high | setns_exec.py:54-70; _setns_libc.py:13-16 | setns.rs:241-251,336-341 | order + `libc::CLONE_NEWUSER/NEWNS/NEWPID/NEWNET` match; missing FDs skipped. |
| 3d | fresh-ns: `unshare -Urm` + uid/gid map + MS_PRIVATE + overlay mount | match | high | namespace_runner.py:227-250; namespace_entrypoint.py:92-135 | fresh_ns.rs:111-145 | `unshare(NEWUSER|NEWNS)`, `setgroups deny`, `uid_map/gid_map 0 <id> 1`, `MS_PRIVATE|REC`, `setsid` (EPERM-tolerant). |
| 3e | setns-overlay-mount: setns(user,mnt) then newest-first lowerdir overlay mount | match | medium | setns_overlay_mount.py:54-86 | setns.rs:99-132; mount.rs:25-60 | order user→mnt, lowerdir newest-first, guard `mem::forget` (mount outlives one-shot helper) ≈ Python helper exit. |
| 4 | Daytona provider parity | missing (intentional) | low | daytona/adapter.py:1-369; daytona/bootstrap.py | ABSENT — no daytona in `eos-sandbox-host/src/` (only a doc mention in registry.rs:144) | Deliberately deferred; agent-core is Docker-only (sandbox.rs:1-3). Reported, not a bug. |

## Disparities

### D1 (low, divergent) — first-call-wins sentinel dropped
Python `bootstrap_sandbox_provider` (bootstrap.py:40-77) is sentinel-gated: the first call wins,
a second call with the *same* provider is a silent no-op, and a second call with a *different*
provider logs a warning and is ignored (bootstrap.py:45-53). The Rust `ProviderRegistry::set_default`
(registry.rs:72-74) is an unconditional `*self.default.write() = Some(adapter)` — last-write-wins, no
sentinel, no "called twice with different provider" warning. The crate doc (registry.rs:5-7) states
this is deliberate ("seeded once at the composition root"). 
- Why it matters: the safety property is preserved only if the composition root truly seeds once.
  The Python guard defended against a second `bootstrap_*` call silently swapping the provider mid-run;
  in Rust nothing prevents a later `set_default` from swapping the live adapter. Risk is low because the
  seam is private to `eos-runtime`, but the defensive invariant is genuinely weaker.
- Suggested fix: either (a) document/enforce single-seed at the composition root with a debug-assert /
  `OnceCell`, or (b) add a "default already set to a different kind" warning in `set_default` to mirror
  the Python warning. No behavior change required if (a).

### D2 (low, divergent) — Daytona is a valid Python config value but a hard parse error in Rust
Python `SandboxConfig.default_provider: Literal["docker","daytona"]` (sandbox.py:41) accepts `"daytona"`
and `bootstrap.py:65-68` routes to the Daytona adapter. Rust `SandboxProvider` (sandbox.rs:14-18) has only
`Docker`, with `#[serde(deny_unknown_fields)]`-adjacent behavior so `"daytona"` *fails to deserialize the
whole config* (sandbox.rs:108-114 test). `resolve_provider_kind` also fails fast with
`UnknownProviderKind("daytona")` (registry.rs:38-41).
- Why it matters: a deployment with `EOS__SANDBOX__DEFAULT_PROVIDER=daytona` (or `EOS_SANDBOX_PROVIDER=daytona`)
  that boots fine on Python will hard-fail config load on Rust. This is expected for the Docker-only migration
  but is a behavioral divergence operators must know about (fail at config-load, not a clear "daytona unsupported"
  runtime message at provider-selection time — the config-deserialize error is less specific).
- Suggested fix: accept the divergence (documented Docker-only scope) but consider surfacing a targeted
  "daytona provider not supported in this build" message rather than a generic deserialize error.

### D3 (medium, divergent — INTENTIONAL relocation, verify on Linux) — ns-side veth config moved from daemon into the holder
In Python, the daemon's `install_veth` (network.py:102-146) configures the namespace-side veth itself by
`nsenter`-ing into the holder pid: `_ip_ns(holder_pid, "link"/"addr"/"route" ...)` sets the link up, assigns
`{ns_ip}/24`, and adds `default via {GATEWAY}`. The holder (`ns_holder.py`) only brings up `lo` and purges IPv6.
The daemon then writes a BARE `b"net-ready\n"` (namespace_runtime.py:205) — no veth args.

In Rust this responsibility is RELOCATED into the holder. The daemon's `install_veth_pair`
(network.rs:1247-1306) only creates the pair, moves the peer into the holder netns, and attaches the host end
to the bridge — it does NOT assign the ns-side address/route. Instead the daemon's `signal_net_ready`
(isolated.rs:307-319) writes `net-ready <ns_name> <ns_ip> 24 <gateway>`, and the holder's `await_net_ready` +
`finish_ready` parse that line (ns-holder/lib.rs:311-327, 298-308) and call `configure_namespace_veth`
(lib.rs:436-444) → set link up, add `/24` addr, add default route via gateway, all via rtnetlink.
- Net effect is the same kernel state; the prefix check still passes (`net-ready ` is a prefix). This is a
  coherent, internally-consistent design change (it removes the daemon's `nsenter` dependency).
- Why it matters: the two halves must stay in lockstep — if a future change reverts the daemon to a bare
  `net-ready\n` without restoring daemon-side ns veth config, the workspace would have a bare interface
  (no IP/route) and egress would silently break (exactly the failure the Python docstring warns about,
  network.py:111-121). Also unverifiable here without a Linux host (see Open Questions #2).
- Suggested fix: none required; add a test asserting `signal_net_ready` always emits the 4-arg form when a
  veth allocation exists, and a holder test (exists: lib.rs:1172-1181 `parse_net_ready_with_optional_veth_config`).

### D4 (low, divergent — Rust STRICTER) — extra bridge-level peer-isolation nft rules in Rust
Python `_install_static_rules` (network.py) installs exactly: NAT table + MASQUERADE; filter table + IMDS
drop; and (opt-in) RFC1918 deny. Per-workspace peer isolation is achieved purely via
`ip link set <host> type bridge_slave isolated on mcast_flood off` (network.py:124-135).
Rust `install_static_rules` (network.rs:374-418) ADDS two rules with no Python equivalent: an inet-family
`nft_peer_isolation_rule_exprs` forward-drop (network.rs:401-406,570-593) AND a whole extra
`eos_iws_bridge_filter` table in the `NFPROTO_BRIDGE` family with a bridge-level peer-isolation drop
(network.rs:407,420-438,596-619), on top of the rtnetlink bridge-port `isolated(true)` (network.rs:1292-1304).
- Why it matters: Rust enforces strictly MORE isolation than the Python ground truth. This is almost certainly
  a hardening improvement (defense in depth for the `bridge_slave isolated` path), but it is a behavioral
  DIVERGENCE from the authoritative spec and could in principle drop traffic the Python design allowed
  (e.g. workspace↔gateway is explicitly excepted via the `!= gateway` clauses, so gateway/MASQUERADE egress
  is preserved; workspace↔workspace is dropped, which matches the isolation intent).
- Suggested fix: confirm with the security owner that the extra rules are intended; if so, fold the same rules
  back into the Python ground truth or note the intentional Rust-only hardening in the migration plan so the
  two are not flagged as accidental drift later.

## Extra findings

- E1 (parity quirk, not a regression). BOTH Python and Rust source the docker run flags from ENV VARS
  (`EOS_DOCKER_DAEMON_TCP`, `EOS_DOCKER_PRIVILEGED`, `EOS_DOCKER_NO_PRIVILEGE`,
  `EOS_DOCKER_DISABLE_OVERLAY_WRITABLE_TMPFS`, `EOS_DOCKER_OVERLAY_WRITABLE_TMPFS_OPTIONS`) —
  Python: client.py:45-69; Rust: docker.rs:478-528. The `DockerConfig.daemon_tcp/privileged/no_privilege`
  config fields (Python sandbox.py:20-23; Rust sandbox.rs:24-33) are defined but NOT consumed by either
  adapter. Faithful port of a pre-existing quirk; worth a note so a future reader doesn't "fix" only one side.

- E2 (match). `generate_auth_token` (docker.rs:490-499) replaces Python `secrets.token_urlsafe(32)`
  (adapter.py:175) with two v4 UUIDs (~244 bits). The exact format is not load-bearing (the daemon reads
  whatever is in `EOS_DAEMON_AUTH_TOKEN`); entropy is comparable. OK.

- E3 (match). `set_labels` Docker semantics preserved on both sides: read current labels, warn if a change
  was requested (sorted keys), return the unchanged container (Python adapter.py:252-276; Rust docker.rs:275-302).

- E4 (match). `is_image_not_found`: Python checks exception type `ImageNotFound` or message substrings
  "no such image"/"image not found" (adapter.py:87-91); Rust checks bollard `DockerResponseServerError`
  with `status_code == 404` OR the same substrings (docker.rs:532-544). The `404` add is the bollard
  equivalent of the typed `ImageNotFound`; behavior equivalent.

- E5 (match). `BridgeAddressPool` range `10.244.0.2 .. 10.244.0.254` (skip .0/.1/.255), lowest-IP-first,
  exhaustion message `isolated_workspace_ip_pool_exhausted` — Python network.py:54-72; Rust network.rs:60-62,
  135-147. `reserve` rejects out-of-range with the same semantics. `_veth_names` = first 6 chars + `h`/`n`
  suffix under IFNAMSIZ=15 (Python network.py:231-235; Rust network.rs:82-90).

- E6 (match). `delete` adapter swallows container-removal errors (best-effort) both sides
  (Python adapter.py:240-250; Rust docker.rs:254-273).

- E7 (note). `daemon_tcp_endpoint` Rust requires a non-empty `host_port` (docker.rs:668-670 filters
  `host_port.as_deref().map(str::is_empty) == Some(false)`); Python only requires truthy `HostPort`
  (adapter.py:316-323). Equivalent for real daemon responses.

## Open questions

1. The Python netns ground truth (ns_holder.py, network.py, setns_*.py, namespace_runtime.py, kernel_mount.py,
   namespace_runner.py) was DELETED from HEAD in commit `37c13f3db`. This audit recovered them from `37c13f3db~1`.
   Is the Python side intended to be fully retired (Rust is now the sole source of truth for netns), or is HEAD
   mid-migration? If retired, the `// PORT backend/src/sandbox/isolated_workspace/...` comments in the Rust crates
   now point at non-existent files and should be re-anchored (e.g. to a git tag) to stay verifiable.
2. The Linux-only paths (ns-holder unshare/handshake, setns, fresh-ns overlay mount, nft rule installation,
   D3 holder-side veth config, D4 extra isolation rules) could NOT be executed in this audit environment (darwin).
   All match claims for invariants 3a-3e and disparities D3/D4 are by source inspection only; they need a Linux
   integration run (the `docker` cargo feature + a kernel host) to confirm runtime behavior.
3. D1: confirm the `eos-runtime` composition root truly seeds `ProviderRegistry::set_default` exactly once so the
   dropped first-call-wins sentinel is harmless.
