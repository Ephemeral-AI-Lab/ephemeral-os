# Go vs Rust for the Sandbox Daemon and Namespace Runner

## Verdict

Use **Rust** for a full sandbox-resident runtime migration when the primary goals are
packaging simplicity, minimal runtime dependencies, and avoiding language-runtime
assumptions inside arbitrary Linux sandbox images.

Go remains a strong fallback if implementation speed and simpler daemon concurrency
matter more than smallest artifact size and tight syscall control. For this project,
network/RPC throughput is not the deciding factor: the daemon mostly uses TCP for
Docker transport, and the current portability problem is the Python runtime bundle,
per-call namespace helper, and reliance on in-image tools.

## Current Problem

The current sandbox daemon/runtime is Python-shaped:

- `backend/src/sandbox/host/daemon_client.py` probes Python candidates such as
  `python3.13`, `python3.12`, `python3.11`, `python3.10`, and `python3`.
- `backend/src/sandbox/host/runtime_bundle.py` uploads a tar.gz of Python modules
  and finalizes it with in-image shell tools such as `tar`.
- `backend/src/sandbox/overlay/namespace_runner.py` launches fresh overlay calls as
  `unshare -Urm <python> -m sandbox.overlay.namespace_entrypoint`.
- `backend/src/sandbox/overlay/namespace_entrypoint.py` mounts the overlay, runs
  the tool primitive, writes result JSON, and unmounts.

That means sandbox images must provide more than "Linux": they need a compatible
Python runtime and enough shell utilities for bootstrap. The migration goal is to
move core daemon/tool execution to artifacts we build and upload.

Target contract:

```text
Sandbox image must provide:
- Linux kernel and matching CPU architecture
- permission to execute uploaded binaries
- kernel features/capabilities required by the selected sandbox mode

Sandbox image should not need for core runtime:
- Python
- Node
- Rust
- Go
- bash
- tar/gzip/base64
- distro package manager
```

Language-specific tools can still exist as optional plugin payloads. For example,
an LSP plugin may need Node for Pyright, but that should not be a dependency of
the core daemon.

## Packaging Comparison

| Criterion | Rust | Go |
| --- | --- | --- |
| Sandbox runtime dependency | None with static Linux binaries | None with `CGO_ENABLED=0` static Linux binaries |
| Artifact shape | One ELF per arch, commonly musl-linked | One ELF per arch, pure-Go static build |
| Typical binary size | Usually smaller | Usually larger because the Go runtime is embedded |
| Typical resident memory | Usually smaller for small helpers/daemons | Usually higher, though still likely better than Python runtime plus imports |
| libc sensitivity | Can target musl for fully static artifacts | Pure Go avoids libc; cgo reintroduces libc/cross-linking concerns |
| External tool dependency | None if implemented with syscalls and Rust libraries | None if implemented with syscalls and Go libraries |
| Cross-arch artifacts | `x86_64-unknown-linux-musl`, `aarch64-unknown-linux-musl` | `GOOS=linux GOARCH=amd64`, `GOOS=linux GOARCH=arm64` |
| Build complexity | Higher, especially musl/aarch64 setup | Lower |
| Best packaging fit | Smallest/minimal dependency surface | Easiest static build workflow |

Both languages still require one artifact per CPU architecture. Static linking
removes runtime/library dependencies; it does not make one CPU binary run on
another CPU architecture.

Suggested Rust artifacts:

```text
eosd-linux-amd64   -> x86_64-unknown-linux-musl
eosd-linux-arm64   -> aarch64-unknown-linux-musl
```

Suggested Go artifacts:

```bash
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o eosd-linux-amd64
CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -o eosd-linux-arm64
```

## Daemon Comparison

The daemon owns the long-lived state machine: request protocol, workspace routing,
LayerStack/OCC operations, background in-flight tracking, audit buffering, plugin
dispatch policy, and isolated workspace lifecycle coordination.

| Criterion | Rust | Go |
| --- | --- | --- |
| Long-lived service ergonomics | Good, but more explicit design | Excellent; goroutines/channels are simple for service plumbing |
| JSON protocol implementation | Strong with `serde`/`serde_json` | Strong with standard library encoding/json |
| Process supervision | Strong, explicit signal/process handling | Strong, simpler APIs |
| Memory behavior | Predictable, no GC | GC-managed; usually fine, less predictable |
| State correctness | Strong type system and ownership help encode invariants | Good type system, easier to write but less strict |
| Implementation speed | Slower for broad daemon port | Faster |
| Maintenance burden | Higher Rust expertise requirement | Lower for most service developers |
| Fit for this project goal | Better when minimizing dependencies and package footprint dominates | Better when migration speed dominates |

Because network/RPC is not the primary bottleneck, Go's daemon-service ergonomics
are useful but not decisive. Rust's stronger packaging and smaller dependency
surface align better with the stated goal.

## Namespace Runner Comparison

The namespace runner is the short-lived, syscall-heavy execution boundary:
`unshare`, `setns`, overlay mount, `execve`, process-group management, timeout,
cancellation, stdout/stderr refs, result JSON, and cleanup.

| Criterion | Rust | Go |
| --- | --- | --- |
| Linux syscall control | Excellent through `rustix`, `nix`, and `libc` | Good through `x/sys/unix` |
| Namespace/thread safety | Direct control, no runtime scheduler surprises for single-threaded helpers | Requires care with `runtime.LockOSThread()` for `setns`/`unshare` |
| Small helper binary | Very good | Usually larger |
| Crash containment | Good if helper is a separate process or subcommand mode | Good if helper is a separate process or subcommand mode |
| Mount/cgroup/netlink work | Strong fit | Good fit, with more runtime considerations |
| Best fit | Rust | Go is acceptable, but less ideal for the syscall-heavy helper |

Rust is the better fit for `eos-ns-runner`. Even if the daemon were Go, the
runner would still be a good Rust candidate. If we want one implementation
language for both, Rust is the cleaner full-migration choice.

## One Binary or Two

There are two independent questions:

1. **Architecture artifacts:** we need one build artifact per CPU architecture
   for both Rust and Go.
2. **Runtime process split:** we may choose one daemon binary with subcommands or
   separate daemon/runner binaries.

Recommended packaging for the first migration:

```text
/opt/eos/bin/eosd
  eosd daemon
  eosd ns-runner
```

This keeps packaging to one binary per architecture while preserving a clean
internal boundary. If the namespace runner later needs a tighter security profile,
we can split it into a second binary without changing the protocol.

## Recommended Dependency Set

Keep the Rust dependency graph intentionally small:

```text
Required:
- serde
- serde_json
- rustix or nix
- libc only for syscall gaps

Optional, only if justified:
- tokio for daemon concurrency
- tracing for structured diagnostics
- thiserror for error definitions
- camino/cap-std only if they materially improve path safety
```

Avoid dependencies that recreate the current portability problem:

- Do not require in-image `bash` for core execution.
- Do not require in-image `tar`, `gzip`, or `base64` for install/finalize.
- Do not shell out to `mount`, `mountpoint`, `ip`, or `nft` for core behavior if
  syscall/netlink implementations are practical.

String shell commands are the one exception: if the user asks to run a shell
string, the sandbox image still needs a shell. To stay image-independent, the
daemon should also support argv-style execution that calls `execve` directly.

## Migration Shape

1. Freeze the daemon JSON protocol with golden request/response fixtures.
2. Add Rust `eosd daemon` with `ready`, `ping`, protocol version, and structured
   diagnostics.
3. Replace runtime upload/finalize with provider file upload plus direct binary
   placement; remove dependence on in-image `tar/gzip/base64` for core runtime.
4. Port direct file verbs and LayerStack read paths.
5. Port OCC write/edit publish behavior.
6. Port fresh overlay shell/search execution through `eosd ns-runner`.
7. Port background in-flight tracking, heartbeat, cancellation, and TTL cleanup.
8. Port isolated workspace enter/run/exit lifecycle.
9. Replace Python-import plugin runtime with an executable plugin protocol:
   stdin JSON request, stdout JSON response.
10. Remove the Python daemon bundle and Python candidate launcher after parity is
    proven.

Do not rewrite TaskCenter, the engine loop, host-facing tool schemas, or public
sandbox APIs as part of this migration. The host should continue to call the same
logical `api.v1.*` operations while the sandbox-resident implementation changes.

## Decision

Choose **Rust** for both the daemon and namespace runner.

Reason:

- It gives the smallest core runtime dependency surface.
- It is better suited to the namespace/mount/exec boundary.
- It should reduce package size and resident memory compared with Python.
- It avoids embedding a larger service runtime when network/RPC is not the
  primary concern.
- It still gives enough daemon structure through conservative crates such as
  `serde`, `serde_json`, and `rustix`.

Choose Go only if the project decides that faster daemon rewrite velocity is more
important than the smallest artifact and tightest syscall-oriented implementation.

## Verification Expectations

Before calling the migration successful:

- `eosd` starts in a Linux image with no Python, Node, Rust, Go, bash, tar, gzip,
  or base64.
- Direct file read/write/edit behavior matches the Python daemon.
- Fresh overlay shell/search behavior matches the Python namespace entrypoint.
- OCC publish and conflict results match existing typed results.
- Background cancellation still kills the full process group.
- Isolated workspace events preserve snapshot, phase timing, discard, and audit
  semantics.
- Audit pull/reporting remains drop-free under the existing sandbox performance
  gates.
- Python daemon and runtime-bundle code paths can be removed rather than kept as
  permanent fallback shims.
