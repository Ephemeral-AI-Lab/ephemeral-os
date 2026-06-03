# Independent verification — Provider / provisioning / network namespace

Area: sandbox — provider selection, provisioning/lifecycle, ns-holder / setns / fresh-ns.
Verifier: workflow-subagent (independent). Date: 2026-06-03.
Method: re-derived every invariant from primary source on BOTH sides. Python ground
truth = in-tree `backend/src/sandbox/provider/**` + `backend/src/config/sections/sandbox.py`
+ `backend/src/runtime/sandbox_provisioning.py`, and the netns/host subsystems recovered at
`/tmp/oldpy/backend/src/sandbox/**` (deleted from HEAD in `37c13f3db`). Rust =
`agent-core/crates/eos-sandbox-host/**`, `agent-core/crates/eos-config/src/sandbox.rs`,
`sandbox/crates/{eos-ns-holder,eos-isolated,eos-daemon,eos-runner}/**`.

Linux-only paths (ns-holder unshare/handshake, setns, fresh-ns, nft, veth) could NOT be
executed (darwin host). Their verdicts are "confirmed by bilateral source derivation,
runtime-unverified on Linux" — not downgraded to unproven, because the source is decisive
and the FD-plumbing/sequencing that joins the two endpoints was also traced (see New Finding NF-1
on the two sequencing checks that could have hidden a false match but did not).

## Invariant verdict table

| # | invariant | independent_status | severity | decisive bilateral anchor |
|---|-----------|--------------------|----------|---------------------------|
| 1a | Docker default; `EOS_SANDBOX_PROVIDER`/config selects provider; Rust Docker-only | confirmed_disparity (by design) | low | PY accepts docker+daytona: `sandbox.py:41` (`Literal["docker","daytona"]`), `bootstrap.py:55-68`. RUST Docker-only: `eos-config/sandbox.rs:14-18,98` rejects `"daytona"` deserialize; `registry.rs:32-41` `resolve_provider_kind` → `UnknownProviderKind("daytona")` |
| 1b | provider bootstrap process-global, first-call-wins | confirmed_disparity (divergent) | low | PY sentinel + 2nd-call-diff warning: `bootstrap.py:17-19,45-53`. RUST `set_default` unconditional last-write-wins: `registry.rs:72-74` (`*self.default.write() = Some(adapter)`). No sentinel/warning. |
| 2a | provisioning: explicit-id starts; else create `request-<8hex>` labelled `origin=workflow,request_id` | confirmed_match | none | PY `sandbox_provisioning.py:56-79` (`request-{uuid4().hex[:8]}`, trim, whitespace→create). RUST `provisioning.rs:31-43,61-87` byte-identical name/labels; "no id" branch eliminated by non-empty `SandboxId`. |
| 2b | lifecycle create/start/stop/delete/set_labels/ensure_running | confirmed_match | none | PY `host/lifecycle.py:19-68` + `host/bootstrap.py:261-289`. RUST `lifecycle.rs:62-146`. ensure_running probe `pwd`→exit0 healthy→else restart+re-setup matches PY `bootstrap.py:261-289`. delete drops plugin-cache cleanup (GC-03, `lifecycle.rs:96-99`) — intentional. |
| 2c | post-lifecycle order: bg upload ∥ ensure_git → drain → authoritative upload → ensure_workspace_base + readiness gate | confirmed_match | none | PY `host/bootstrap.py:325-337` (A start_upload→B ensure_git→C finish→D run_bootstrap→E ensure_workspace_base). RUST `lifecycle.rs:150-172` identical A-E. Gate PY `bootstrap.py:192-202` == RUST `lifecycle.rs:364-377` (`ready==true ∧ control_plane ok ∧ manifest_version>=1`). |
| 2d | Docker caps/tmpfs/init host-config | confirmed_match | high | PY `docker/client.py:25-42,40-42`. RUST `docker.rs:44-45,501-530`. `SYS_ADMIN`+`NET_ADMIN`, `seccomp=unconfined`+`apparmor=unconfined`, `init=true`, tmpfs `/eos`=`rw,exec,size=2g,mode=1777`. Byte-identical (grepped). |
| 2e | Docker daemon-TCP env/labels + endpoint | confirmed_match | medium | PY `docker/adapter.py:28-34,170-183,289-339`. RUST `docker.rs:35-45,147-168,643-686`. port `37657`, label/env names, `host_port` empty filtered, `0.0.0.0`/`::`/empty→`127.0.0.1` all match. |
| 3a | ns-holder pins ns FDs, pauses until SIGTERM | confirmed_match (source; runtime-unverified Linux) | high | PY `ns_holder.py:89-115` + daemon `namespace_runtime.py:118-125` (opens `pid_for_children`). RUST `ns-holder/lib.rs:823-851` `unshare(NEWUSER\|NEWNS\|NEWPID\|NEWNET)`, pins `/proc/self/ns/{user,mnt,pid_for_children,net}` in RAII `HeldNamespaces`, `lib.rs:891-898` `pause()` loop. Daemon `eos-daemon/isolated.rs:204-207` opens `pid_for_children` for the `pid` key (NF-1 Check 1). |
| 3b | 2-step pipe handshake `ns-up`→(`net-ready` prefix)→`ready` | confirmed_match (source; runtime-unverified) | medium | PY `ns_holder.py:94,100-111` (`startswith(b"net-ready")`, EOF→1, wrong→2). RUST `lib.rs:61,67,71,261-277` (`starts_with(NET_READY)`, EOF→ControlPipeClosed/exit1, else UnexpectedToken/exit2). |
| 3c | setns order user→mnt→pid→net; PID before fork; CLONE_* constants | confirmed_match (source; runtime-unverified) | high | PY `setns_exec.py:55-66,79` + `_setns_libc.py:13-16`. RUST `setns.rs:234-244,330-334` order+`libc::CLONE_NEWUSER/NEWNS/NEWPID/NEWNET`, missing FDs filtered; fork in `fresh_ns::execute_tool` after `join_namespaces`. |
| 3d | fresh-ns `unshare -Urm`+uid/gid map+MS_PRIVATE+overlay | confirmed_match (source; runtime-unverified) | high | PY `namespace_runner`/`entrypoint` pattern. RUST `fresh_ns.rs:107-141`: `setsid` (EPERM-tolerant), `unshare(NEWUSER\|NEWNS)`, `setgroups deny`, `uid_map/gid_map "0 <id> 1"`, `MS_PRIVATE\|REC`. |
| 3e | setns-overlay-mount: setns(user,mnt) then newest-first lowerdir mount | confirmed_match (source; runtime-unverified) | medium | PY `setns_overlay_mount.py:55-85` (user→mnt; lowerdirs from payload as-is). RUST `setns.rs:94-126` user→mnt then `MountInputs`+`mem::forget(guard)` (mount outlives one-shot helper ≈ PY exit). Newest-first ordering is upstream of both helpers (daemon-supplied) and preserved identically; `mount.rs:24-34` documents newest-first. |
| 4 | Daytona provider parity | confirmed_disparity (missing, intentional) | low | PY `daytona/adapter.py` present. RUST absent (only doc mentions). Docker-only scope (`eos-config/sandbox.rs:1-3`). Reported, not a bug. |

Tally (invariant checklist rows only): 10 confirmed_match (2a-2e, 3a-3e), 3 confirmed_disparity
(1a, 1b, 4), 0 unproven, 0 investigator_missed. The runtime-unverified 3a-3e count as
confirmed_match (the Linux caveat is a qualifier, not a separate bucket). D1-D4 are the separate
"Disparity adjudication" section and overlap the table rows (D1≈1b, D2≈1a), so they are NOT
re-counted here. The production-wiring gap is a NEW FINDING about reachability, not a parity break
— see NF-2.

## Disparity adjudication

### D1 (first-call-wins sentinel dropped) — CONFIRMED as stated; OQ#3 RESOLVED (and reframed by NF-2)
Refuted nothing. PY `bootstrap.py:45-53` is sentinel-gated with a "called twice with different
provider" warning; RUST `registry.rs:72-74` is unconditional last-write-wins, no warning. Verified.
OQ#3 asked whether the composition root seeds `set_default` exactly once. Independent grep
(`agent-core` whole-repo, `*.rs`): EVERY `set_default` caller is inside `#[cfg(test)]`
(`registry.rs:169,203`; `daemon_client.rs:1000,1176`; `provisioning.rs:103`; `lifecycle.rs:411,460`;
`runtime_artifact.rs:369`; `isolated_workspace.rs:380`). The production composition root
`app_state.rs:444` builds `ProviderRegistry::new()` and never calls `set_default`. So the
dropped-sentinel risk is presently moot — there is no production seed to swap (see NF-2). D1 stays
low/divergent; it would only matter once a real seed path exists. Keep the investigator's suggested
fix (debug-assert/OnceCell or a "default already set" warning).

### D2 (Daytona valid in PY config, hard parse error in Rust) — CONFIRMED as stated
PY `sandbox.py:41` Literal accepts `"daytona"`; RUST `eos-config/sandbox.rs:98` test proves
`serde_yaml::from_str::<SandboxProvider>("daytona").is_err()`, and `registry.rs:38-41`
fails fast. Behavioral divergence operators must know (config-load fail vs. a targeted
"daytona unsupported" message). Intentional Docker-only scope. Verified.

### D3 (ns-side veth config relocated daemon→holder) — CONFIRMED; lockstep verified across the orchestration boundary
Refuted nothing; strengthened. PY: daemon `install_veth` (network.py:102-146) configures ns-side
via `nsenter`/`_ip_ns` (link up, `{ns_ip}/24`, default via GATEWAY); holder only brings up `lo`
+ purges IPv6; daemon writes BARE `b"net-ready\n"` (namespace_runtime.py:205).
RUST: daemon `install_veth_pair` (eos-isolated/network.rs:1240-1293) only creates the pair, moves
the peer into the holder netns (`setns_by_pid`, line 1258), attaches host end to bridge, sets bridge
port isolation — does NOT assign ns-side IP/route. Daemon `signal_net_ready`
(eos-daemon/isolated.rs:307-318) emits the 4-arg `net-ready <ns_name> <ns_ip> <BRIDGE_PREFIX_LEN>
<GATEWAY>\n` when `handle.veth` is `Some`. Holder parses it (`lib.rs:299-315`) and runs
`configure_namespace_veth` (`lib.rs:421-429`).
DECISIVE lockstep check (the false-match risk): the peer iface must exist in the holder netns
BEFORE `signal_net_ready`. Verified bilaterally: PY `workspace_handle_lifecycle.py:147-194` and
RUST `eos-isolated/session.rs:841-886` BOTH order spawn_ns_holder → open_ns_fds → install_veth →
mount_overlay → configure_dns → signal_net_ready. `install_veth` precedes `signal_net_ready` on both
sides, so the holder's best-effort `configure_namespace_veth` never runs against a missing iface.
D3 holds.

### D4 (extra Rust-only peer-isolation nft rules) — CONFIRMED; Rust STRICTER, with gateway exception preserved
Refuted nothing. PY `_install_static_rules` (network.py:186-223): NAT MASQUERADE + IMDS drop +
opt-in RFC1918 deny only; per-workspace isolation purely via bridge-port `bridge_slave isolated on
mcast_flood off` (network.py:122-135). RUST `install_static_rules`
(eos-isolated/network.rs:362-405) ADDS: (1) inet-family `nft_peer_isolation_rule_exprs` forward-drop
(network.rs:389-393,557-580) and (2) a whole `eos_iws_bridge_filter` table in NFPROTO_BRIDGE with a
bridge-level drop (network.rs:394,407-425,583-606), ON TOP of the same rtnetlink bridge-port
`isolated(true)` (network.rs:1279-1291). Both extra rules carry `saddr != gateway`/`daddr != gateway`
clauses (network.rs:562-577,588-603) so workspace↔gateway/MASQUERADE egress is preserved while
workspace↔workspace is dropped — matches the isolation intent. Rust-only hardening; flag for the
security owner per the investigator's suggested fix.

## New findings

### NF-1 (verifier-added, NOT a defect) — two FD-plumbing/sequencing false-match risks checked and CLEARED
Because 3a/3c/D3 join two endpoints across the eos-protocol/daemon boundary, a false match could
hide in the connective tissue even when both endpoints look correct. Two source-checkable risks were
hunted and cleared:
- Check 1 (pidns identity): the runner `setns(pid_fd, CLONE_NEWPID)` only isolates correctly if the
  daemon opens `/proc/<holder_pid>/ns/pid_for_children`, not `.../ns/pid`. Verified: RUST daemon
  `eos-daemon/isolated.rs:206` opens `pid_for_children` (matches PY `namespace_runtime.py:123`). Had
  it opened `.../ns/pid`, the runner would have joined the OUTER pidns — a silent isolation break.
- Check 2 (orchestration order for D3): verified above — `install_veth` precedes `signal_net_ready`
  on both sides (`session.rs:856-886` / `workspace_handle_lifecycle.py:157-189`).

### NF-2 (verifier-added, MEDIUM — wiring/integration gap, corollary of investigator OQ#1) — the Docker provider seam is unit-tested but NOT wired into the production binary
The investigation rates 1a/1b/2a/2b as parity "match," which is correct at the unit level (the
ported functions do exactly what is claimed; their unit tests pass). But the seam is never
activated by the shipped binary:
- Production entrypoint `agent-core/crates/eos-runtime/src/main.rs:20` calls
  `AppState::builder().build()` with NO injected transport/provisioner/registry.
- The default build (`app_state.rs:444-457`) constructs `ProviderRegistry::new()` (default = None),
  a default `DaemonClient` over it, and a default `HostProvisioner`→`RequestSandboxProvisioner`→
  `SandboxLifecycle` over the SAME empty registry.
- Nothing in non-test code calls `set_default(...)`, `register(...)` (prod), or
  `resolve_provider_kind(...)`. The only non-test `.transport()/.provisioner()` injection
  (`app_state.rs:806-809`) installs a `FakeProvisioner`/`FakeTransport` and is itself a test helper.
- Consequence in the shipped binary: a fresh-sandbox request reaches
  `RequestSandboxProvisioner::prepare_for_run` → `lifecycle.create` → `registry().default()?` →
  `Err(NoDefaultProvider)` (`lifecycle.rs:63`, `registry.rs:78-83`). The explicit-id path hits
  `adapter(id)?` → also `NoDefaultProvider` (no binding, no default).

Classification: this is NOT `investigator_missed` against the four invariants (the code is not
broken; it does what the parity claims say). It is a reachability/wiring-completeness gap and a
direct corollary of the investigator's own Open Question #1 (HEAD is mid-migration; netns Python was
just deleted). Severity MEDIUM because a real binary (`main.rs`) drives `AppState` but cannot
currently provision a Docker sandbox; LOW-ish if the agent-core binary is not yet the live request
path. Recommended: (a) add the composition-root seed
(`provider_registry.set_default(Arc::new(DockerProviderAdapter::new(...)))` gated on
`resolve_provider_kind`) when the migration reaches the wiring step, and (b) when that seed lands,
revisit D1 — the dropped first-call-wins sentinel becomes live-relevant the moment a real
`set_default` exists.

### NF-3 (verifier-added, INFORMATIONAL — anchor imprecision) — host bootstrap/lifecycle are recoverable source, not docs-only
The investigation's invariant table cites 2b/2c as "host/lifecycle.py (per docs 300-322)" and
"host/bootstrap.py:300-312 (per docs)," treating the host layer as docs-corroborated only. Those
files ARE recoverable at `/tmp/oldpy/backend/src/sandbox/host/{bootstrap.py,lifecycle.py}` and were
verified directly here. The behavioral claims are correct, but the cited line numbers do not match
the actual files (`bootstrap.py` is 362 lines; `setup_post_lifecycle` is at 325-337,
`_require_workspace_base_ready` at 192-202; `lifecycle.py` CRUD at 19-68). Re-anchor 2b/2c to the
recovered source (or a git tag) so the claims stay verifiable. Same re-anchoring applies to OQ#1's
note about the deleted netns `// PORT` comments.

## Overall verdict

The investigation is SOUND and unusually careful. Every "match" verdict (2a-2e, 3a-3e) was
independently re-derived and holds — including the two cross-boundary sequencing risks (NF-1) that
could have hidden a false match in 3a/3c/D3 but did not. All four flagged disparities (D1-D4) are
CONFIRMED with bilateral anchors; none were overstated. Constants on the high/medium-severity
surfaces (2d caps/tmpfs, 2e daemon-TCP, E5 bridge pool/CIDR/nft table names) are byte-identical.

The one thing the investigation under-weighted is reachability: the Docker provider seam (1a/1b/2a/2b)
is faithfully ported and unit-tested but is NOT seeded into the production composition root
(`main.rs` → unseeded `ProviderRegistry`), so the shipped binary cannot currently provision a Docker
sandbox (NF-2). This does not flip the parity verdicts — the units match — but it is a real
integration gap and a corollary of the investigator's own OQ#1 (mid-migration HEAD). It also defuses
D1 for now (no production seed to swap) and should be paired with the D1 fix when wiring lands.

Linux-only behavior (3a-3e, D3 holder veth config, D4 nft rules) is confirmed by source only; a
Linux integration run (the `docker`/Linux cargo features + a kernel host) is still required to
confirm runtime behavior, exactly as the investigator's OQ#2 states.
