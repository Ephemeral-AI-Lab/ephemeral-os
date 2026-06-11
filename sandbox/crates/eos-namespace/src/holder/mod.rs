//! Isolated-workspace namespace holder.
//!
//! The holder unshares the user/mount/pid/net namespace stack, pins namespace
//! FDs for the daemon, completes the readiness/control pipe handshake, then
//! pauses until teardown.

mod namespace;
mod network;

use std::os::fd::RawFd;

use namespace::{rbind_proc, unshare_namespace_stack, HeldNamespaces};
use network::{
    bring_loopback_up, configure_namespace_veth, disable_ipv6_ra, flush_ipv6_default_route,
    parse_network_config, NetworkConfig,
};

/// Readiness handshake token (`b"ns-up\n"`) written to the readiness FD once the
/// holder is inside the new namespace stack.
pub const NS_UP: &[u8] = b"ns-up\n";

/// Control-pipe token the daemon writes once the network is wired.
///
/// The holder requires the newline-terminated control read to *start with* this
/// prefix; it is a `startswith` check, not an equality compare.
pub const NET_READY: &[u8] = b"net-ready";

/// Final readiness token (`b"ready\n"`) written to the readiness FD after the
/// current best-effort network hardening hooks.
pub const READY: &[u8] = b"ready\n";

/// Test-only holder crash knob.
///
/// When set to `"true"`, the holder exits with
/// [`NsHolderError::TEST_CRASH_EXIT`] after writing [`NS_UP`] and before
/// reading the control pipe, to exercise the daemon's holder-crash recovery
/// path.
pub const TEST_HOLDER_CRASH_ENV: &str = "EOS_ISOLATED_WORKSPACE_TEST_HOLDER_CRASH";

/// Failures raised by the holder lifecycle.
///
/// The variants carry the holder's exit-code contract so the daemon-side
/// recovery logic (and `eosd`'s `main`) can map them to process exit codes
/// without re-deriving them: the exit codes below, plus `SIGTERM` exiting 0.
#[derive(Debug, thiserror::Error)]
#[non_exhaustive]
pub enum NsHolderError {
    /// `unshare` of the namespace stack failed before the handshake could start.
    #[error("failed to unshare namespace stack")]
    Unshare,
    /// The control pipe reached EOF before a full token arrived.
    #[error("control pipe closed before net-ready")]
    ControlPipeClosed,
    /// The control pipe delivered a line that did not start with [`NET_READY`].
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
    pub const CONTROL_CLOSED_EXIT: i32 = 1;
    /// Exit code for [`NsHolderError::UnexpectedToken`].
    pub const UNEXPECTED_TOKEN_EXIT: i32 = 2;
    /// Exit code for the test-only crash knob.
    pub const TEST_CRASH_EXIT: i32 = 7;
}

#[derive(Debug)]
struct Handshake {
    readiness_fd: RawFd,
    control_fd: RawFd,
    network_config: Option<NetworkConfig>,
    _namespaces: HeldNamespaces,
}

impl Handshake {
    const fn new(readiness_fd: RawFd, control_fd: RawFd, namespaces: HeldNamespaces) -> Self {
        Self {
            readiness_fd,
            control_fd,
            network_config: None,
            _namespaces: namespaces,
        }
    }

    fn signal_ns_up(&mut self) -> Result<(), NsHolderError> {
        write_all_fd(self.readiness_fd, NS_UP)
    }

    fn await_net_ready(&mut self) -> Result<(), NsHolderError> {
        let mut buf = [0_u8; 256];
        let mut offset = 0;
        while offset < buf.len() {
            let read = read_fd(self.control_fd, &mut buf[offset..offset + 1])?;
            if read == 0 {
                return Err(NsHolderError::ControlPipeClosed);
            }
            offset += read;
            if buf[offset - 1] == b'\n' {
                break;
            }
        }
        if !buf[..offset].starts_with(NET_READY) {
            return Err(NsHolderError::UnexpectedToken);
        }
        self.network_config = parse_network_config(&buf[..offset]);
        Ok(())
    }

    fn finish_ready(&self) -> Result<(), NsHolderError> {
        bring_loopback_up();
        if let Some(config) = &self.network_config {
            configure_namespace_veth(config);
        }
        disable_ipv6_ra();
        flush_ipv6_default_route();
        write_all_fd(self.readiness_fd, READY)
    }
}

/// Run the holder lifecycle over inherited readiness/control pipe FDs.
///
/// # Errors
///
/// Returns [`NsHolderError`] when namespace setup or the readiness handshake
/// fails.
pub fn run(readiness_fd: RawFd, control_fd: RawFd) -> Result<(), NsHolderError> {
    let namespaces = unshare_namespace_stack(readiness_fd, control_fd)?;
    rbind_proc();
    let mut handshake = Handshake::new(readiness_fd, control_fd, namespaces);
    handshake.signal_ns_up()?;
    if std::env::var(TEST_HOLDER_CRASH_ENV)
        .unwrap_or_default()
        .eq_ignore_ascii_case("true")
    {
        return Err(NsHolderError::TestCrash);
    }
    handshake.await_net_ready()?;
    handshake.finish_ready()?;
    loop {
        // SAFETY: `pause(2)` has no pointer arguments and simply suspends this
        // single-threaded holder process until a signal is delivered.
        unsafe {
            libc::pause();
        }
    }
}

fn write_all_fd(fd: RawFd, mut bytes: &[u8]) -> Result<(), NsHolderError> {
    while !bytes.is_empty() {
        // SAFETY: `bytes.as_ptr()` is valid for `bytes.len()` bytes and the
        // inherited fd is borrowed for the duration of the syscall.
        let written = unsafe { libc::write(fd, bytes.as_ptr().cast(), bytes.len()) };
        if written < 0 {
            let err = std::io::Error::last_os_error();
            if err.kind() == std::io::ErrorKind::Interrupted {
                continue;
            }
            return Err(NsHolderError::PipeIo(err));
        }
        let written = usize::try_from(written).map_err(|_| {
            NsHolderError::PipeIo(std::io::Error::other("negative write byte count"))
        })?;
        bytes = &bytes[written..];
    }
    Ok(())
}

fn read_fd(fd: RawFd, bytes: &mut [u8]) -> Result<usize, NsHolderError> {
    loop {
        // SAFETY: `bytes.as_mut_ptr()` is valid for `bytes.len()` bytes and the
        // inherited fd is borrowed for the duration of the syscall.
        let read = unsafe { libc::read(fd, bytes.as_mut_ptr().cast(), bytes.len()) };
        if read >= 0 {
            return usize::try_from(read).map_err(|_| {
                NsHolderError::PipeIo(std::io::Error::other("negative read byte count"))
            });
        }
        let err = std::io::Error::last_os_error();
        if err.kind() != std::io::ErrorKind::Interrupted {
            return Err(NsHolderError::PipeIo(err));
        }
    }
}

#[cfg(test)]
#[path = "../../tests/unit/holder/handshake.rs"]
mod tests;
