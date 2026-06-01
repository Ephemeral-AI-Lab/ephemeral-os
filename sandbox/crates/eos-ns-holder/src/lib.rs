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
//! host because Linux syscall bodies are gated by `cfg(target_os = "linux")`.
//!
//! # Handshake (1:1 with `ns_holder.py`)
//!
//! 1. write [`NS_UP`] (`"ns-up\n"`) to the readiness FD once we are inside the
//!    new namespace stack; the daemon then opens our ns symlinks and wires the
//!    veth/bridge network.
//! 2. read the control FD until newline and require it to start with
//!    [`NET_READY`] (`"net-ready"`) — a PREFIX check, not equality.
//! 3. apply best-effort loopback and IPv6 hardening hooks, then write [`READY`]
//!    (`"ready\n"`) to the readiness FD.
//! 4. `pause()` until `SIGTERM`, then exit 0.
//!
//! Syscall crate — `unsafe` is permitted here for raw libc gaps, and every
//! `unsafe` block carries a focused `// SAFETY:` note.
#![deny(unsafe_op_in_unsafe_fn)]

#[cfg(target_os = "linux")]
use std::ffi::{c_void, CString};
use std::fs;
use std::net::Ipv4Addr;
#[cfg(target_os = "linux")]
use std::os::fd::FromRawFd;
use std::os::fd::{OwnedFd, RawFd};
use std::path::Path;
#[cfg(target_os = "linux")]
use std::thread;
#[cfg(target_os = "linux")]
use std::time::Duration;

#[cfg(target_os = "linux")]
use rustix::mount::{mount_change, MountPropagationFlags};
#[cfg(target_os = "linux")]
use rustix::thread::{set_thread_gid, set_thread_uid, unshare, UnshareFlags};

/// Readiness handshake token written to the readiness FD once the holder is
/// inside the new namespace stack. PORT `ns_holder.py:94` (`b"ns-up\n"`).
pub const NS_UP: &[u8] = b"ns-up\n";

/// Control-pipe token the daemon writes once the network is wired. The holder
/// requires the newline-terminated control read to *start with* this prefix —
/// it is a `startswith` check, not an equality compare.
/// PORT `ns_holder.py:106` (`buf.startswith(b"net-ready")`).
pub const NET_READY: &[u8] = b"net-ready";

/// Final readiness token written to the readiness FD after the current
/// best-effort network hardening hooks. PORT `ns_holder.py:111` (`b"ready\n"`).
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
    /// Namespace setup opened/wrote a procfs control file unsuccessfully.
    #[error("namespace setup io failed at {path}")]
    SetupIo {
        /// Path being opened or written when namespace setup failed.
        path: String,
        /// Underlying I/O failure.
        #[source]
        source: std::io::Error,
    },
    /// Test-only holder crash injection fired after `ns-up`.
    #[error("test holder crash injected")]
    TestCrash,
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
    /// PID namespace-for-children FD (`/proc/self/ns/pid_for_children`).
    pub pid: OwnedFd,
    /// Network namespace FD (`/proc/self/ns/net`).
    pub net: OwnedFd,
    #[cfg(target_os = "linux")]
    _pid_init: Option<PidNamespaceInit>,
}

#[cfg(target_os = "linux")]
#[derive(Debug)]
struct PidNamespaceInit {
    pid: libc::pid_t,
}

#[cfg(target_os = "linux")]
impl Drop for PidNamespaceInit {
    fn drop(&mut self) {
        // SAFETY: `pid` came from `fork` in this process. Sending SIGTERM and
        // reaping with WNOHANG are best-effort cleanup for error paths; the
        // child also has PR_SET_PDEATHSIG for abrupt holder termination.
        unsafe {
            libc::kill(self.pid, libc::SIGTERM);
            let mut status = 0;
            libc::waitpid(self.pid, &mut status, libc::WNOHANG);
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct NetworkConfig {
    iface: String,
    ns_ip: Ipv4Addr,
    prefix_len: u8,
    gateway: Ipv4Addr,
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
    /// Best-effort network hardening applied, [`READY`] written to the readiness FD.
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
    network_config: Option<NetworkConfig>,
    _namespaces: HeldNamespaces,
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
            network_config: None,
            _namespaces: namespaces,
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
        write_all_fd(self.readiness_fd, NS_UP)?;
        self.state = HandshakeState::NsUpSent;
        Ok(())
    }

    /// Read the control FD until newline and require a [`NET_READY`] prefix
    /// (handshake step 2). EOF before a token → [`NsHolderError::ControlPipeClosed`];
    /// a non-matching token → [`NsHolderError::UnexpectedToken`].
    // PORT backend/src/sandbox/isolated_workspace/scripts/ns_holder.py:100-107 — read 64-byte chunks until b"\n", reject EOF (exit 1) / wrong prefix (exit 2)
    pub fn await_net_ready(&mut self) -> Result<(), NsHolderError> {
        let mut buf = Vec::new();
        while !buf.contains(&b'\n') {
            let mut chunk = [0_u8; 64];
            let read = read_fd(self.control_fd, &mut chunk)?;
            if read == 0 {
                return Err(NsHolderError::ControlPipeClosed);
            }
            buf.extend_from_slice(&chunk[..read]);
        }
        if !buf.starts_with(NET_READY) {
            return Err(NsHolderError::UnexpectedToken);
        }
        self.network_config = parse_network_config(&buf);
        self.state = HandshakeState::NetReadyReceived;
        Ok(())
    }

    /// Apply best-effort loopback and IPv6 hardening, then write [`READY`]
    /// (handshake step 3) and advance to [`HandshakeState::Ready`].
    // PORT backend/src/sandbox/isolated_workspace/scripts/ns_holder.py:109-111 — `ip link set lo up`, _purge_ipv6_default_routes(), os.write(readiness_fd, b"ready\n")
    pub fn finish_ready(&mut self) -> Result<(), NsHolderError> {
        bring_loopback_up();
        if let Some(config) = self.network_config.as_ref() {
            configure_namespace_veth(config);
        }
        disable_ipv6_ra();
        flush_ipv6_default_route();
        write_all_fd(self.readiness_fd, READY)?;
        self.state = HandshakeState::Ready;
        Ok(())
    }
}

fn parse_network_config(buf: &[u8]) -> Option<NetworkConfig> {
    let line = std::str::from_utf8(buf).ok()?.trim();
    let mut parts = line.split_whitespace();
    if parts.next()? != "net-ready" {
        return None;
    }
    let iface = parts.next()?.to_owned();
    let ns_ip = parts.next()?.parse().ok()?;
    let prefix_len = parts.next()?.parse().ok()?;
    let gateway = parts.next()?.parse().ok()?;
    Some(NetworkConfig {
        iface,
        ns_ip,
        prefix_len,
        gateway,
    })
}

/// Recursively bind the parent's `/proc` over the inherited `/proc` so setns'd
/// shells inside the new mount namespace see a usable `/proc/self`.
///
/// Best-effort, shell-free: replaces the Python `subprocess.run(["mount",
/// "--rbind", "/proc", "/proc"], check=False)` with a raw `mount(MS_BIND |
/// MS_REC)` syscall. Failure must NOT abort the holder.
// PORT backend/src/sandbox/isolated_workspace/scripts/ns_holder.py:81-86 — mount --rbind /proc /proc, best-effort (check=False)
fn rbind_proc() {
    #[cfg(target_os = "linux")]
    {
        let proc = b"/proc\0";
        // SAFETY: both source and target are static NUL-terminated strings, the
        // filesystem type and data pointers are null as required for a bind
        // mount, and failure is intentionally ignored to preserve Python's
        // best-effort `mount --rbind /proc /proc` behavior.
        let _ = unsafe {
            libc::mount(
                proc.as_ptr().cast(),
                proc.as_ptr().cast(),
                std::ptr::null::<libc::c_char>(),
                (libc::MS_BIND | libc::MS_REC) as libc::c_ulong,
                std::ptr::null::<c_void>(),
            )
        };
    }
}

/// Disable IPv6 router-advertisement acceptance on every interface, shell-free.
///
/// Replaces `sysctl -w net.ipv6.conf.{iface}.accept_ra=0` with a write of `"0"`
/// to `/proc/sys/net/ipv6/conf/{iface}/accept_ra`, iterating [`IPV6_CONF_ROOT`]
/// (falling back to [`FALLBACK_IPV6_CONF_INTERFACES`]). Best-effort per iface.
// PORT backend/src/sandbox/isolated_workspace/scripts/ns_holder.py:39 — sysctl -w net.ipv6.conf.{iface}.accept_ra=0 → write /proc/sys, shell-free
fn disable_ipv6_ra() {
    let mut interfaces = Vec::new();
    if let Ok(entries) = fs::read_dir(IPV6_CONF_ROOT) {
        interfaces.extend(
            entries
                .flatten()
                .filter_map(|entry| entry.file_name().into_string().ok()),
        );
    }
    if interfaces.is_empty() {
        interfaces.extend(
            FALLBACK_IPV6_CONF_INTERFACES
                .iter()
                .map(|iface| iface.to_string()),
        );
    }
    for iface in interfaces {
        let _ = fs::write(
            Path::new(IPV6_CONF_ROOT).join(iface).join("accept_ra"),
            b"0",
        );
    }
}

/// Bring loopback up through rtnetlink, shell-free.
///
/// Replaces `ip link set lo up` with `RTM_NEWLINK` so holder readiness does not
/// depend on `ip(8)` being present inside the image. Best-effort.
// PORT backend/src/sandbox/isolated_workspace/scripts/ns_holder.py:109 — ip link set lo up
fn bring_loopback_up() {
    #[cfg(target_os = "linux")]
    {
        let Ok(lo) = CString::new("lo") else {
            return;
        };
        // SAFETY: `lo` is a valid NUL-terminated C string and `if_nametoindex`
        // does not retain the pointer after returning.
        let index = unsafe { libc::if_nametoindex(lo.as_ptr()) };
        if index == 0 {
            return;
        }
        let msg = IfInfoMsg {
            ifi_family: libc::AF_UNSPEC as u8,
            ifi_pad: 0,
            ifi_type: 0,
            ifi_index: index as i32,
            ifi_flags: libc::IFF_UP as u32,
            ifi_change: libc::IFF_UP as u32,
        };
        let _ = send_netlink_message(libc::RTM_NEWLINK, netlink_request_flags(), &msg);
    }
}

/// Configure the namespace-side veth after the daemon moved it into this netns.
///
/// The daemon owns veth creation and host-side bridge attachment. The holder is
/// already in the target netns, so it configures the peer's link state, address,
/// and default route without `nsenter(1)` or `ip(8)`. Best-effort.
fn configure_namespace_veth(config: &NetworkConfig) {
    #[cfg(not(target_os = "linux"))]
    let _ = config;
    #[cfg(target_os = "linux")]
    {
        let index = link_index(&config.iface);
        if index == 0 {
            return;
        }
        set_link_up(index);
        add_ipv4_address(index, config.ns_ip, config.prefix_len);
        add_ipv4_default_route(index, config.gateway);
    }
}

#[cfg(target_os = "linux")]
fn link_index(name: &str) -> libc::c_uint {
    let Ok(name) = CString::new(name) else {
        return 0;
    };
    // SAFETY: `name` is a valid NUL-terminated C string and `if_nametoindex`
    // does not retain the pointer after returning.
    unsafe { libc::if_nametoindex(name.as_ptr()) }
}

#[cfg(target_os = "linux")]
fn set_link_up(index: libc::c_uint) {
    let msg = IfInfoMsg {
        ifi_family: libc::AF_UNSPEC as u8,
        ifi_pad: 0,
        ifi_type: 0,
        ifi_index: index as i32,
        ifi_flags: libc::IFF_UP as u32,
        ifi_change: libc::IFF_UP as u32,
    };
    let _ = send_netlink_message(libc::RTM_NEWLINK, netlink_request_flags(), &msg);
}

#[cfg(target_os = "linux")]
fn add_ipv4_address(index: libc::c_uint, ip: Ipv4Addr, prefix_len: u8) {
    let msg = IfAddrMsg {
        ifa_family: libc::AF_INET as u8,
        ifa_prefixlen: prefix_len,
        ifa_flags: 0,
        ifa_scope: 0,
        ifa_index: index,
    };
    let attrs = [
        NetlinkAttr::new(IFA_ADDRESS, ip.octets().to_vec()),
        NetlinkAttr::new(IFA_LOCAL, ip.octets().to_vec()),
    ];
    let _ =
        send_netlink_message_with_attrs(libc::RTM_NEWADDR, netlink_create_flags(), &msg, &attrs);
}

#[cfg(target_os = "linux")]
fn add_ipv4_default_route(index: libc::c_uint, gateway: Ipv4Addr) {
    let route = RouteMsg {
        rtm_family: libc::AF_INET as u8,
        rtm_dst_len: 0,
        rtm_src_len: 0,
        rtm_tos: 0,
        rtm_table: libc::RT_TABLE_MAIN,
        rtm_protocol: libc::RTPROT_STATIC,
        rtm_scope: libc::RT_SCOPE_UNIVERSE,
        rtm_type: libc::RTN_UNICAST,
        rtm_flags: 0,
    };
    let attrs = [
        NetlinkAttr::new(RTA_GATEWAY, gateway.octets().to_vec()),
        NetlinkAttr::new(RTA_OIF, index.to_ne_bytes().to_vec()),
    ];
    let _ =
        send_netlink_message_with_attrs(libc::RTM_NEWROUTE, netlink_create_flags(), &route, &attrs);
}

/// Flush the IPv6 default route via rtnetlink, shell-free.
///
/// Replaces `ip -6 route flush default` with a netlink `RTM_DELROUTE` (or
/// dump+delete) so no bridge-side RA can repopulate a v6 default route and
/// bypass the v4-only MASQUERADE filter. Best-effort.
// PORT backend/src/sandbox/isolated_workspace/scripts/ns_holder.py:45 — ip -6 route flush default → rtnetlink RTM_DELROUTE, shell-free
fn flush_ipv6_default_route() {
    #[cfg(target_os = "linux")]
    {
        let route = RouteMsg {
            rtm_family: libc::AF_INET6 as u8,
            rtm_dst_len: 0,
            rtm_src_len: 0,
            rtm_tos: 0,
            rtm_table: libc::RT_TABLE_MAIN,
            rtm_protocol: libc::RTPROT_UNSPEC,
            rtm_scope: libc::RT_SCOPE_UNIVERSE,
            rtm_type: libc::RTN_UNICAST,
            rtm_flags: 0,
        };
        let _ = send_netlink_message(libc::RTM_DELROUTE, netlink_request_flags(), &route);
    }
}

#[cfg(target_os = "linux")]
fn netlink_request_flags() -> u16 {
    (libc::NLM_F_REQUEST | libc::NLM_F_ACK) as u16
}

#[cfg(target_os = "linux")]
fn netlink_create_flags() -> u16 {
    (libc::NLM_F_REQUEST | libc::NLM_F_ACK | libc::NLM_F_CREATE | libc::NLM_F_EXCL) as u16
}

#[cfg(target_os = "linux")]
fn send_netlink_message<T>(
    message_type: u16,
    flags: u16,
    payload: &T,
) -> Result<(), std::io::Error> {
    send_netlink_message_with_attrs(message_type, flags, payload, &[])
}

#[cfg(target_os = "linux")]
fn send_netlink_message_with_attrs<T>(
    message_type: u16,
    flags: u16,
    payload: &T,
    attrs: &[NetlinkAttr],
) -> Result<(), std::io::Error> {
    let length = std::mem::size_of::<libc::nlmsghdr>() + std::mem::size_of::<T>();
    let attrs_len: usize = attrs
        .iter()
        .map(|attr| align4(RTATTR_HEADER_LEN + attr.value.len()))
        .sum();
    let mut message = Vec::with_capacity(length + attrs_len);
    let header = libc::nlmsghdr {
        nlmsg_len: (length + attrs_len) as u32,
        nlmsg_type: message_type,
        nlmsg_flags: flags,
        nlmsg_seq: 1,
        nlmsg_pid: 0,
    };
    append_struct_bytes(&mut message, &header);
    append_struct_bytes(&mut message, payload);
    for attr in attrs {
        append_attr(&mut message, attr);
    }
    let addr = NetlinkSocketAddress {
        nl_family: libc::AF_NETLINK as libc::sa_family_t,
        nl_pad: 0,
        nl_pid: 0,
        nl_groups: 0,
    };
    // SAFETY: `socket` is called with constant arguments and returns an owned fd
    // on success, closed below before returning.
    let fd = unsafe {
        libc::socket(
            libc::AF_NETLINK,
            libc::SOCK_RAW | libc::SOCK_CLOEXEC,
            libc::NETLINK_ROUTE,
        )
    };
    if fd < 0 {
        return Err(std::io::Error::last_os_error());
    }
    // SAFETY: `message` and `addr` are valid for the duration of this call; the
    // kernel copies the bytes before returning. The fd is a netlink socket just
    // opened by this function.
    let rc = unsafe {
        libc::sendto(
            fd,
            message.as_ptr().cast(),
            message.len(),
            0,
            (&addr as *const NetlinkSocketAddress).cast(),
            std::mem::size_of::<NetlinkSocketAddress>() as libc::socklen_t,
        )
    };
    let result = if rc < 0 {
        Err(std::io::Error::last_os_error())
    } else {
        Ok(())
    };
    // SAFETY: `fd` is owned by this function after a successful `socket` call.
    let _ = unsafe { libc::close(fd) };
    result
}

#[cfg(target_os = "linux")]
fn append_struct_bytes<T>(buffer: &mut Vec<u8>, value: &T) {
    // SAFETY: `value` is valid for `size_of::<T>()` bytes, and the bytes are
    // copied immediately into `buffer` without outliving `value`.
    let bytes = unsafe {
        std::slice::from_raw_parts((value as *const T).cast::<u8>(), std::mem::size_of::<T>())
    };
    buffer.extend_from_slice(bytes);
}

#[cfg(target_os = "linux")]
fn append_attr(buffer: &mut Vec<u8>, attr: &NetlinkAttr) {
    let length = RTATTR_HEADER_LEN + attr.value.len();
    buffer.extend_from_slice(&(length as u16).to_ne_bytes());
    buffer.extend_from_slice(&attr.kind.to_ne_bytes());
    buffer.extend_from_slice(&attr.value);
    let padded = align4(length);
    buffer.resize(buffer.len() + padded - length, 0);
}

#[cfg(target_os = "linux")]
fn align4(length: usize) -> usize {
    (length + 3) & !3
}

#[cfg(target_os = "linux")]
const RTATTR_HEADER_LEN: usize = 4;
#[cfg(target_os = "linux")]
const IFA_ADDRESS: u16 = 1;
#[cfg(target_os = "linux")]
const IFA_LOCAL: u16 = 2;
#[cfg(target_os = "linux")]
const RTA_OIF: u16 = 4;
#[cfg(target_os = "linux")]
const RTA_GATEWAY: u16 = 5;

#[cfg(target_os = "linux")]
struct NetlinkAttr {
    kind: u16,
    value: Vec<u8>,
}

#[cfg(target_os = "linux")]
impl NetlinkAttr {
    fn new(kind: u16, value: Vec<u8>) -> Self {
        Self { kind, value }
    }
}

#[cfg(target_os = "linux")]
#[repr(C)]
struct IfInfoMsg {
    ifi_family: u8,
    ifi_pad: u8,
    ifi_type: u16,
    ifi_index: i32,
    ifi_flags: u32,
    ifi_change: u32,
}

#[cfg(target_os = "linux")]
#[repr(C)]
struct IfAddrMsg {
    ifa_family: u8,
    ifa_prefixlen: u8,
    ifa_flags: u8,
    ifa_scope: u8,
    ifa_index: u32,
}

#[cfg(target_os = "linux")]
#[repr(C)]
struct RouteMsg {
    rtm_family: u8,
    rtm_dst_len: u8,
    rtm_src_len: u8,
    rtm_tos: u8,
    rtm_table: u8,
    rtm_protocol: u8,
    rtm_scope: u8,
    rtm_type: u8,
    rtm_flags: u32,
}

#[cfg(target_os = "linux")]
#[repr(C)]
struct NetlinkSocketAddress {
    nl_family: libc::sa_family_t,
    nl_pad: u16,
    nl_pid: u32,
    nl_groups: u32,
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
/// # Safety
///
/// This function MUST run on a single-threaded process; the kernel rejects
/// `CLONE_NEWUSER` in a multithreaded process. The crate deliberately has no
/// tokio dependency and is invoked through the dedicated `eosd ns-holder`
/// subprocess.
// PORT backend/src/sandbox/isolated_workspace/_control_plane/namespace_runtime.py:84-96 — `unshare --user --map-root-user --net --pid --mount --fork --kill-child --propagation private` consolidated into a single unshare(CLONE_NEWUSER|NEWNS|NEWPID|NEWNET) + uid/gid map + MS_PRIVATE in-process
#[cfg(target_os = "linux")]
fn unshare_namespace_stack(
    readiness_fd: RawFd,
    control_fd: RawFd,
) -> Result<HeldNamespaces, NsHolderError> {
    let host_uid = rustix::process::getuid().as_raw();
    let host_gid = rustix::process::getgid().as_raw();
    unshare(
        UnshareFlags::NEWUSER | UnshareFlags::NEWNS | UnshareFlags::NEWPID | UnshareFlags::NEWNET,
    )
    .map_err(|_| NsHolderError::Unshare)?;
    write_if_exists("/proc/self/setgroups", b"deny\n")?;
    write_setup_file("/proc/self/uid_map", format!("0 {host_uid} 1\n").as_bytes())?;
    write_setup_file("/proc/self/gid_map", format!("0 {host_gid} 1\n").as_bytes())?;
    set_thread_gid(rustix::process::Gid::ROOT).map_err(|_| NsHolderError::Unshare)?;
    set_thread_uid(rustix::process::Uid::ROOT).map_err(|_| NsHolderError::Unshare)?;
    mount_change(
        "/",
        MountPropagationFlags::PRIVATE | MountPropagationFlags::REC,
    )
    .map_err(|_| NsHolderError::Unshare)?;
    let pid_init = fork_pid_namespace_init(readiness_fd, control_fd)?;
    wait_for_path("/proc/self/ns/pid_for_children")?;
    Ok(HeldNamespaces {
        user: open_owned_fd("/proc/self/ns/user")?,
        mnt: open_owned_fd("/proc/self/ns/mnt")?,
        pid: open_owned_fd("/proc/self/ns/pid_for_children")?,
        net: open_owned_fd("/proc/self/ns/net")?,
        _pid_init: Some(pid_init),
    })
}

#[cfg(not(target_os = "linux"))]
fn unshare_namespace_stack(
    _readiness_fd: RawFd,
    _control_fd: RawFd,
) -> Result<HeldNamespaces, NsHolderError> {
    Err(NsHolderError::Unshare)
}

/// Holder entry point: mirrors `ns_holder.py:main(argv)` but takes the two
/// already-parsed pipe FDs (argv → FD parsing stays in `eosd`'s `main`, per the
/// lib/main split). Returns once `SIGTERM` is received.
///
/// Sequence: [`unshare_namespace_stack`] → [`rbind_proc`] → write [`NS_UP`] →
/// (test-crash knob) → await [`NET_READY`] → best-effort network hardening →
/// write [`READY`] → install a `SIGTERM` handler and `pause()`.
// PORT backend/src/sandbox/isolated_workspace/scripts/ns_holder.py:89-115 — main(argv): rbind /proc, ns-up, crash-knob, net-ready read, lo up + purge, ready, SIGTERM handler + signal.pause()
pub fn run(readiness_fd: RawFd, control_fd: RawFd) -> Result<(), NsHolderError> {
    let namespaces = unshare_namespace_stack(readiness_fd, control_fd)?;
    rbind_proc();
    let mut handshake = Handshake::new(readiness_fd, control_fd, namespaces);
    handshake.state = HandshakeState::ProcBound;
    handshake.signal_ns_up()?;
    if std::env::var(TEST_HOLDER_CRASH_ENV)
        .unwrap_or_default()
        .eq_ignore_ascii_case("true")
    {
        return Err(NsHolderError::TestCrash);
    }
    handshake.await_net_ready()?;
    handshake.finish_ready()?;
    handshake.state = HandshakeState::Paused;
    loop {
        // SAFETY: `pause(2)` has no pointer arguments and simply suspends this
        // single-threaded holder process until a signal is delivered. The
        // daemon terminates the holder with SIGTERM/SIGKILL during teardown.
        unsafe {
            libc::pause();
        }
    }
}

fn write_all_fd(fd: RawFd, mut bytes: &[u8]) -> Result<(), NsHolderError> {
    while !bytes.is_empty() {
        // SAFETY: `bytes.as_ptr()` is valid for `bytes.len()` bytes for the
        // duration of this call, and `fd` is an inherited pipe descriptor owned
        // by the process that launched this single-threaded holder.
        let written = unsafe { libc::write(fd, bytes.as_ptr().cast(), bytes.len()) };
        if written < 0 {
            let err = std::io::Error::last_os_error();
            if err.kind() == std::io::ErrorKind::Interrupted {
                continue;
            }
            return Err(NsHolderError::PipeIo(err));
        }
        if written == 0 {
            return Err(NsHolderError::PipeIo(std::io::Error::new(
                std::io::ErrorKind::WriteZero,
                "pipe write returned zero",
            )));
        }
        bytes = &bytes[written as usize..];
    }
    Ok(())
}

fn read_fd(fd: RawFd, bytes: &mut [u8]) -> Result<usize, NsHolderError> {
    loop {
        // SAFETY: `bytes.as_mut_ptr()` is valid for `bytes.len()` bytes for the
        // duration of this call, and `fd` is an inherited pipe descriptor owned
        // by the process that launched this single-threaded holder.
        let read = unsafe { libc::read(fd, bytes.as_mut_ptr().cast(), bytes.len()) };
        if read >= 0 {
            return Ok(read as usize);
        }
        let err = std::io::Error::last_os_error();
        if err.kind() != std::io::ErrorKind::Interrupted {
            return Err(NsHolderError::PipeIo(err));
        }
    }
}

#[cfg(target_os = "linux")]
fn write_if_exists(path: impl AsRef<Path>, value: &[u8]) -> Result<(), NsHolderError> {
    let path = path.as_ref();
    match fs::write(path, value) {
        Ok(()) => Ok(()),
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(err) => Err(setup_io(path, err)),
    }
}

#[cfg(target_os = "linux")]
fn open_owned_fd(path: impl AsRef<Path>) -> Result<OwnedFd, NsHolderError> {
    let path = path.as_ref();
    let file = fs::File::open(path).map_err(|err| setup_io(path, err))?;
    let raw_fd = std::os::fd::IntoRawFd::into_raw_fd(file);
    // SAFETY: `raw_fd` came from `File::into_raw_fd`, so this function becomes
    // the sole owner and closes it through `OwnedFd` drop.
    Ok(unsafe { OwnedFd::from_raw_fd(raw_fd) })
}

#[cfg(target_os = "linux")]
fn write_setup_file(path: impl AsRef<Path>, value: &[u8]) -> Result<(), NsHolderError> {
    let path = path.as_ref();
    fs::write(path, value).map_err(|err| setup_io(path, err))
}

#[cfg(target_os = "linux")]
fn setup_io(path: &Path, source: std::io::Error) -> NsHolderError {
    NsHolderError::SetupIo {
        path: path.display().to_string(),
        source,
    }
}

#[cfg(target_os = "linux")]
fn fork_pid_namespace_init(
    readiness_fd: RawFd,
    control_fd: RawFd,
) -> Result<PidNamespaceInit, NsHolderError> {
    // SAFETY: The holder is a dedicated single-threaded process. `fork` is used
    // here to reproduce `unshare --pid --fork`: the first child becomes PID 1 in
    // the new PID namespace, which materializes `/proc/self/ns/pid_for_children`
    // for the parent holder to pin and later hand to ns-runner children.
    let pid = unsafe { libc::fork() };
    if pid < 0 {
        return Err(NsHolderError::Unshare);
    }
    if pid == 0 {
        run_pid_namespace_init(readiness_fd, control_fd);
    }
    Ok(PidNamespaceInit { pid })
}

#[cfg(target_os = "linux")]
fn run_pid_namespace_init(readiness_fd: RawFd, control_fd: RawFd) -> ! {
    // SAFETY: The child does not participate in the handshake and must not keep
    // inherited pipe descriptors open; closing the standard descriptors is not
    // necessary because the daemon starts ns-holder with stdio redirected.
    unsafe {
        libc::close(readiness_fd);
        libc::close(control_fd);
        libc::signal(
            libc::SIGTERM,
            exit_signal_handler as *const () as libc::sighandler_t,
        );
        libc::signal(
            libc::SIGINT,
            exit_signal_handler as *const () as libc::sighandler_t,
        );
        libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGTERM, 0, 0, 0);
        if libc::getppid() == 1 {
            libc::_exit(0);
        }
    }
    loop {
        // SAFETY: `pause` has no pointer arguments and simply waits for a
        // signal. The SIGTERM/SIGINT handler above exits this PID-namespace init.
        unsafe {
            libc::pause();
        }
    }
}

#[cfg(target_os = "linux")]
extern "C" fn exit_signal_handler(_signal: libc::c_int) {
    // SAFETY: `_exit` is async-signal-safe and terminates the PID-namespace init
    // without running Rust destructors from inside a signal handler.
    unsafe {
        libc::_exit(0);
    }
}

#[cfg(target_os = "linux")]
fn wait_for_path(path: impl AsRef<Path>) -> Result<(), NsHolderError> {
    let path = path.as_ref();
    for _ in 0..100 {
        if path.exists() {
            return Ok(());
        }
        thread::sleep(Duration::from_millis(10));
    }
    open_owned_fd(path).map(drop)
}

#[cfg(test)]
mod tests {
    use std::os::fd::{AsRawFd, OwnedFd};

    use super::{
        parse_network_config, Handshake, HandshakeState, HeldNamespaces, NsHolderError, NS_UP,
        READY,
    };

    #[test]
    fn signal_ns_up_writes_readiness_token() {
        let (readiness_read, readiness_write) = nix::unistd::pipe().expect("readiness pipe");
        let (_control_read, control_write) = nix::unistd::pipe().expect("control pipe");
        let mut handshake = Handshake::new(
            readiness_write.as_raw_fd(),
            control_write.as_raw_fd(),
            dummy_namespaces(),
        );

        handshake.signal_ns_up().expect("ns-up write succeeds");

        let mut buf = [0_u8; 16];
        let read = nix::unistd::read(readiness_read.as_raw_fd(), &mut buf).expect("read ns-up");
        assert_eq!(&buf[..read], NS_UP);
        assert_eq!(handshake.state(), HandshakeState::NsUpSent);
    }

    #[test]
    fn await_net_ready_accepts_prefixed_line() {
        let (_readiness_read, readiness_write) = nix::unistd::pipe().expect("readiness pipe");
        let (control_read, control_write) = nix::unistd::pipe().expect("control pipe");
        nix::unistd::write(&control_write, b"net-ready extra\n").expect("write control token");
        let mut handshake = Handshake::new(
            readiness_write.as_raw_fd(),
            control_read.as_raw_fd(),
            dummy_namespaces(),
        );

        handshake.await_net_ready().expect("net-ready accepted");

        assert_eq!(handshake.state(), HandshakeState::NetReadyReceived);
    }

    #[test]
    fn await_net_ready_rejects_wrong_token() {
        let (_readiness_read, readiness_write) = nix::unistd::pipe().expect("readiness pipe");
        let (control_read, control_write) = nix::unistd::pipe().expect("control pipe");
        nix::unistd::write(&control_write, b"wrong\n").expect("write control token");
        let mut handshake = Handshake::new(
            readiness_write.as_raw_fd(),
            control_read.as_raw_fd(),
            dummy_namespaces(),
        );

        let error = handshake
            .await_net_ready()
            .expect_err("wrong token rejected");

        assert!(matches!(error, NsHolderError::UnexpectedToken));
    }

    #[test]
    fn finish_ready_writes_ready_token() {
        let (readiness_read, readiness_write) = nix::unistd::pipe().expect("readiness pipe");
        let (_control_read, control_write) = nix::unistd::pipe().expect("control pipe");
        let mut handshake = Handshake::new(
            readiness_write.as_raw_fd(),
            control_write.as_raw_fd(),
            dummy_namespaces(),
        );

        handshake.finish_ready().expect("ready write succeeds");

        let mut buf = [0_u8; 16];
        let read = nix::unistd::read(readiness_read.as_raw_fd(), &mut buf).expect("read ready");
        assert_eq!(&buf[..read], READY);
        assert_eq!(handshake.state(), HandshakeState::Ready);
    }

    #[test]
    fn parse_net_ready_with_optional_veth_config() {
        let config = parse_network_config(b"net-ready eos-iws-abcden 10.244.0.2 24 10.244.0.1\n")
            .expect("network config parses");

        assert_eq!(config.iface, "eos-iws-abcden");
        assert_eq!(config.ns_ip.to_string(), "10.244.0.2");
        assert_eq!(config.prefix_len, 24);
        assert_eq!(config.gateway.to_string(), "10.244.0.1");
    }

    fn dummy_namespaces() -> HeldNamespaces {
        HeldNamespaces {
            user: dev_null_fd(),
            mnt: dev_null_fd(),
            pid: dev_null_fd(),
            net: dev_null_fd(),
            #[cfg(target_os = "linux")]
            _pid_init: None,
        }
    }

    fn dev_null_fd() -> OwnedFd {
        std::fs::File::open("/dev/null")
            .expect("open /dev/null")
            .into()
    }
}
