//! Namespace holder: the dedicated single-threaded child that creates and pins
//! the isolated workspace's namespace stack and runs the readiness handshake.
//!
//! # Architecture invariant
//!
//! While still single-threaded, this process `unshare`s the full namespace
//! stack (`CLONE_NEWUSER | CLONE_NEWNS | CLONE_NEWPID | CLONE_NEWNET`), holds
//! the resulting namespace FDs open for the daemon to wire into, runs the
//! readiness/control pipe handshake, then `pause()`s until `SIGTERM`.
//!
//! The daemon NEVER enters a namespace itself — it stays multi-threaded (tokio)
//! and would fail `unshare(CLONE_NEWUSER)` / `setns` into a user namespace,
//! which the kernel requires the calling task to be single-threaded for. This
//! dedicated child is the one that crosses that boundary, so the daemon can
//! later open `/proc/{holder_pid}/ns/{net,pid,mnt,user}` against a stable PID 1
//! of the pidns.
//!
//! # Build-time guarantee
//!
//! This is a true near-leaf: it links only `eos-protocol` (and only if the
//! handshake tokens are ever shared — today they are inline byte literals, so
//! the tokens below are owned here). It deliberately pulls in NO tokio: the
//! single-threaded `unshare(CLONE_NEWUSER)` requirement is a kernel constraint,
//! not a style choice. Linux-only at runtime; the skeleton compiles on the dev
//! host because every syscall body is a bare `todo!()`.
//!
//! # Handshake (1:1 with `ns_holder.py`)
//!
//! 1. write [`NS_UP`] (`"ns-up\n"`) to the readiness FD once we are inside the
//!    new namespace stack; the daemon then opens our ns symlinks and wires the
//!    veth/bridge network.
//! 2. read the control FD until newline and require it to start with
//!    [`NET_READY`] (`"net-ready"`) — a PREFIX check, not equality.
//! 3. bring `lo` up, purge IPv6 default routes / disable RA acceptance, then
//!    write [`READY`] (`"ready\n"`) to the readiness FD.
//! 4. `pause()` until `SIGTERM`, then exit 0.
//!
//! Syscall crate — `unsafe` is permitted here for the raw namespace syscalls;
//! every future `unsafe` block must carry a `// SAFETY:` note and every public
//! `unsafe fn` a `# Safety` section. No real `unsafe` is present yet; the future
//! syscalls are documented in `// PORT` prose on each `todo!()`.
#![deny(unsafe_op_in_unsafe_fn)]

use std::os::fd::{OwnedFd, RawFd};

/// Readiness handshake token written to the readiness FD once the holder is
/// inside the new namespace stack. PORT `ns_holder.py:94` (`b"ns-up\n"`).
pub const NS_UP: &[u8] = b"ns-up\n";

/// Control-pipe token the daemon writes once the network is wired. The holder
/// requires the newline-terminated control read to *start with* this prefix —
/// it is a `startswith` check, not an equality compare.
/// PORT `ns_holder.py:106` (`buf.startswith(b"net-ready")`).
pub const NET_READY: &[u8] = b"net-ready";

/// Final readiness token written to the readiness FD after `lo` is up and the
/// IPv6 default routes are purged. PORT `ns_holder.py:111` (`b"ready\n"`).
pub const READY: &[u8] = b"ready\n";

/// Test-only environment knob: when set to `"true"`, the holder exits with
/// [`NsHolderError::TEST_CRASH_EXIT`] after writing [`NS_UP`] and before reading
/// the control pipe, to exercise the daemon's holder-crash recovery path.
/// PORT `ns_holder.py:97` (`EOS_ISOLATED_WORKSPACE_TEST_HOLDER_CRASH`).
pub const TEST_HOLDER_CRASH_ENV: &str = "EOS_ISOLATED_WORKSPACE_TEST_HOLDER_CRASH";

/// `/proc` subtree the holder enumerates to find per-interface IPv6 config dirs.
/// PORT `ns_holder.py:25` (`_IPV6_CONF_ROOT`).
pub const IPV6_CONF_ROOT: &str = "/proc/sys/net/ipv6/conf";

/// Interface names tried when `/proc/sys/net/ipv6/conf` cannot be listed.
/// PORT `ns_holder.py:26` (`_FALLBACK_IPV6_CONF_INTERFACES`).
pub const FALLBACK_IPV6_CONF_INTERFACES: [&str; 4] = ["all", "default", "lo", "eth0"];

/// Failures raised by the holder lifecycle.
///
/// The variants carry the holder's exit-code contract so the daemon-side
/// recovery logic (and `eosd`'s `main`) can map them to process exit codes
/// without re-deriving them. PORT `ns_holder.py:98/104/107` (the `return 7/1/2`
/// arms) and `ns_holder.py:113-114` (`SIGTERM` → `sys.exit(0)`).
#[derive(Debug, thiserror::Error)]
#[non_exhaustive]
pub enum NsHolderError {
    /// `unshare` of the namespace stack failed before the handshake could start.
    #[error("failed to unshare namespace stack")]
    Unshare,
    /// The control pipe reached EOF before a full token arrived.
    /// PORT `ns_holder.py:103-104` (`if not chunk: return 1`).
    #[error("control pipe closed before net-ready")]
    ControlPipeClosed,
    /// The control pipe delivered a line that did not start with [`NET_READY`].
    /// PORT `ns_holder.py:106-107` (`if not buf.startswith(...): return 2`).
    #[error("control pipe sent unexpected token; expected net-ready prefix")]
    UnexpectedToken,
    /// Writing a readiness token or reading the control pipe failed.
    #[error("handshake pipe i/o failed")]
    PipeIo(#[source] std::io::Error),
}

impl NsHolderError {
    /// Exit code for [`NsHolderError::ControlPipeClosed`].
    /// PORT `ns_holder.py:104` (`return 1`).
    pub const CONTROL_CLOSED_EXIT: i32 = 1;
    /// Exit code for [`NsHolderError::UnexpectedToken`].
    /// PORT `ns_holder.py:107` (`return 2`).
    pub const UNEXPECTED_TOKEN_EXIT: i32 = 2;
    /// Exit code for the test-only crash knob.
    /// PORT `ns_holder.py:98` (`return 7`).
    pub const TEST_CRASH_EXIT: i32 = 7;
}

/// The namespace FDs the holder pins open for its whole lifetime.
///
/// Wrapping [`OwnedFd`] gives RAII close-on-drop for free with zero `unsafe`:
/// when the holder process exits the kernel tears the namespaces down once the
/// last referencing FD (and the holder task) is gone. The daemon reads the
/// matching `/proc/{holder_pid}/ns/*` symlinks while this struct keeps the
/// holder alive. PORT `_control_plane/namespace_runtime.py:118` (`open_ns_fds`,
/// the daemon side that opens these symlinks against the live holder).
#[derive(Debug)]
pub struct HeldNamespaces {
    /// User namespace FD (`/proc/self/ns/user`).
    pub user: OwnedFd,
    /// Mount namespace FD (`/proc/self/ns/mnt`).
    pub mnt: OwnedFd,
    /// PID namespace FD (`/proc/self/ns/pid`).
    pub pid: OwnedFd,
    /// Network namespace FD (`/proc/self/ns/net`).
    pub net: OwnedFd,
}

/// Where the handshake driver currently is, mirroring the linear flow in
/// `ns_holder.py:main` (`:89-115`). The transitions are total and ordered:
/// `Unshared → ProcBound → NsUpSent → NetReadyReceived → Ready → Paused`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[non_exhaustive]
pub enum HandshakeState {
    /// Namespace stack `unshare`d; FDs not yet pinned.
    Unshared,
    /// Parent `/proc` recursively bound into the new mount namespace.
    ProcBound,
    /// [`NS_UP`] written to the readiness FD.
    NsUpSent,
    /// A [`NET_READY`]-prefixed line was read from the control FD.
    NetReadyReceived,
    /// `lo` up, IPv6 routes purged, [`READY`] written to the readiness FD.
    Ready,
    /// `pause()`ing until `SIGTERM`.
    Paused,
}

/// Drives the readiness/control handshake over a pair of inherited pipe FDs.
///
/// Holds the pinned [`HeldNamespaces`] so they outlive the handshake, and
/// tracks the current [`HandshakeState`]. The pipe FDs are passed as `RawFd`
/// because they are inherited (not owned) — the daemon owns the other ends and
/// closes them; the holder reads/writes but does not own their lifetime.
#[derive(Debug)]
pub struct Handshake {
    readiness_fd: RawFd,
    control_fd: RawFd,
    state: HandshakeState,
    namespaces: HeldNamespaces,
}

impl Handshake {
    /// Build a handshake driver over the inherited pipe FDs and the freshly
    /// pinned namespaces, starting in [`HandshakeState::Unshared`]. The pipe FDs
    /// are inherited (the daemon owns the far ends), so they are passed as
    /// `RawFd`, not `OwnedFd`.
    pub fn new(readiness_fd: RawFd, control_fd: RawFd, namespaces: HeldNamespaces) -> Self {
        Self {
            readiness_fd,
            control_fd,
            state: HandshakeState::Unshared,
            namespaces,
        }
    }

    /// The current handshake position.
    pub fn state(&self) -> HandshakeState {
        self.state
    }

    /// Write [`NS_UP`] to the readiness FD (handshake step 1) and advance to
    /// [`HandshakeState::NsUpSent`].
    // PORT backend/src/sandbox/isolated_workspace/scripts/ns_holder.py:94 — os.write(readiness_fd, b"ns-up\n") after the /proc rbind
    pub fn signal_ns_up(&mut self) -> Result<(), NsHolderError> {
        todo!("PORT ns_holder.py:94 — write NS_UP to readiness_fd, set state NsUpSent")
    }

    /// Read the control FD until newline and require a [`NET_READY`] prefix
    /// (handshake step 2). EOF before a token → [`NsHolderError::ControlPipeClosed`];
    /// a non-matching token → [`NsHolderError::UnexpectedToken`].
    // PORT backend/src/sandbox/isolated_workspace/scripts/ns_holder.py:100-107 — read 64-byte chunks until b"\n", reject EOF (exit 1) / wrong prefix (exit 2)
    pub fn await_net_ready(&mut self) -> Result<(), NsHolderError> {
        todo!(
            "PORT ns_holder.py:100-107 — accumulate control_fd reads, startswith(NET_READY) check"
        )
    }

    /// Bring `lo` up, purge IPv6 default routes / disable RA acceptance, then
    /// write [`READY`] (handshake step 3) and advance to [`HandshakeState::Ready`].
    // PORT backend/src/sandbox/isolated_workspace/scripts/ns_holder.py:109-111 — `ip link set lo up`, _purge_ipv6_default_routes(), os.write(readiness_fd, b"ready\n")
    pub fn finish_ready(&mut self) -> Result<(), NsHolderError> {
        todo!("PORT ns_holder.py:109-111 — lo up + purge IPv6 + write READY")
    }
}

/// Recursively bind the parent's `/proc` over the inherited `/proc` so setns'd
/// shells inside the new mount namespace see a usable `/proc/self`.
///
/// Best-effort, shell-free: replaces the Python `subprocess.run(["mount",
/// "--rbind", "/proc", "/proc"], check=False)` with a raw `mount(MS_BIND |
/// MS_REC)` syscall. Failure must NOT abort the holder.
// PORT backend/src/sandbox/isolated_workspace/scripts/ns_holder.py:81-86 — mount --rbind /proc /proc, best-effort (check=False)
fn rbind_proc() {
    todo!("PORT ns_holder.py:81-86 — raw mount(MS_BIND|MS_REC) of /proc, ignore errors")
}

/// Disable IPv6 router-advertisement acceptance on every interface, shell-free.
///
/// Replaces `sysctl -w net.ipv6.conf.{iface}.accept_ra=0` with a write of `"0"`
/// to `/proc/sys/net/ipv6/conf/{iface}/accept_ra`, iterating [`IPV6_CONF_ROOT`]
/// (falling back to [`FALLBACK_IPV6_CONF_INTERFACES`]). Best-effort per iface.
// PORT backend/src/sandbox/isolated_workspace/scripts/ns_holder.py:39 — sysctl -w net.ipv6.conf.{iface}.accept_ra=0 → write /proc/sys, shell-free
fn disable_ipv6_ra() {
    todo!("PORT ns_holder.py:39 — write 0 to /proc/sys/net/ipv6/conf/<iface>/accept_ra per iface")
}

/// Flush the IPv6 default route via rtnetlink, shell-free.
///
/// Replaces `ip -6 route flush default` with a netlink `RTM_DELROUTE` (or
/// dump+delete) so no bridge-side RA can repopulate a v6 default route and
/// bypass the v4-only MASQUERADE filter. Best-effort.
// PORT backend/src/sandbox/isolated_workspace/scripts/ns_holder.py:45 — ip -6 route flush default → rtnetlink RTM_DELROUTE, shell-free
fn flush_ipv6_default_route() {
    todo!("PORT ns_holder.py:45 — rtnetlink delete of the IPv6 default route(s)")
}

/// `unshare` the full namespace stack on the calling (single-threaded) task and
/// pin the resulting `/proc/self/ns/*` FDs.
///
/// This is the Rust *consolidation* of the launcher's `unshare(1)` flags: the
/// daemon today spawns `ns_holder.py` via
/// `unshare --user --map-root-user --net --pid --mount --fork --kill-child
/// --propagation private`, so the namespaces are created by the `unshare`
/// binary, not inside `ns_holder.py`. The Rust holder owns that step directly:
/// `unshare(CLONE_NEWUSER | CLONE_NEWNS | CLONE_NEWPID | CLONE_NEWNET)` plus the
/// uid/gid map writes and `MS_PRIVATE` mount-propagation, then opens its own
/// `ns/{user,mnt,pid,net}` symlinks into a [`HeldNamespaces`].
///
/// # Safety (future)
///
/// The real body will call raw `unshare`/`mount` syscalls; it MUST run on a
/// single-threaded process (kernel requirement for `CLONE_NEWUSER`), so this
/// crate forbids tokio.
// PORT backend/src/sandbox/isolated_workspace/_control_plane/namespace_runtime.py:84-96 — `unshare --user --map-root-user --net --pid --mount --fork --kill-child --propagation private` consolidated into a single unshare(CLONE_NEWUSER|NEWNS|NEWPID|NEWNET) + uid/gid map + MS_PRIVATE in-process
fn unshare_namespace_stack() -> Result<HeldNamespaces, NsHolderError> {
    todo!("PORT namespace_runtime.py:84-96 — unshare(CLONE_NEWUSER|NEWNS|NEWPID|NEWNET), write uid/gid maps, pin ns FDs")
}

/// Holder entry point: mirrors `ns_holder.py:main(argv)` but takes the two
/// already-parsed pipe FDs (argv → FD parsing stays in `eosd`'s `main`, per the
/// lib/main split). Returns once `SIGTERM` is received.
///
/// Sequence: [`unshare_namespace_stack`] → [`rbind_proc`] → write [`NS_UP`] →
/// (test-crash knob) → await [`NET_READY`] → `lo` up + IPv6 purge → write
/// [`READY`] → install a `SIGTERM` handler and `pause()`.
// PORT backend/src/sandbox/isolated_workspace/scripts/ns_holder.py:89-115 — main(argv): rbind /proc, ns-up, crash-knob, net-ready read, lo up + purge, ready, SIGTERM handler + signal.pause()
pub fn run(readiness_fd: RawFd, control_fd: RawFd) -> Result<(), NsHolderError> {
    todo!("PORT ns_holder.py:89-115 — full holder lifecycle: unshare, handshake, pause() until SIGTERM")
}
