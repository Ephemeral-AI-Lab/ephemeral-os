use std::collections::HashMap;
#[cfg(unix)]
use std::fs::File;
#[cfg(target_os = "linux")]
use std::fs::OpenOptions;
#[cfg(target_os = "linux")]
use std::io::Write;
#[cfg(unix)]
use std::os::fd::IntoRawFd;
#[cfg(target_os = "linux")]
use std::os::fd::{AsRawFd, RawFd};
#[cfg(target_os = "linux")]
use std::thread;
#[cfg(target_os = "linux")]
use std::time::{Duration, Instant};

#[cfg(target_os = "linux")]
use linux_namespace_subprocess::protocol::{Fd, NsFds};
#[cfg(target_os = "linux")]
use nix::errno::Errno;
#[cfg(target_os = "linux")]
use nix::fcntl::{fcntl, FcntlArg, FdFlag, OFlag};
#[cfg(target_os = "linux")]
use nix::unistd::read;

use crate::profile::IsolatedNetworkError;

#[cfg(target_os = "linux")]
use super::setup_error;
use super::{NamespacePlan, NamespaceRuntime};

impl NamespaceRuntime {
    pub(crate) fn open_ns_fds(
        &self,
        holder_pid: i32,
        plan: NamespacePlan,
    ) -> Result<HashMap<String, i32>, IsolatedNetworkError> {
        if self.stub {
            return open_stub_ns_fds(plan);
        }
        if holder_pid <= 0 {
            return Ok(HashMap::new());
        }
        #[cfg(not(target_os = "linux"))]
        {
            let _ = (holder_pid, plan);
            Ok(HashMap::new())
        }
        #[cfg(target_os = "linux")]
        {
            let mut opened = Vec::new();
            for name in plan.fd_names() {
                opened.push((
                    name.to_owned(),
                    open_inheritable_fd(namespace_fd_path(holder_pid, name))?,
                ));
            }
            Ok(into_raw_fd_map(opened))
        }
    }
}

#[cfg(unix)]
fn open_stub_ns_fds(plan: NamespacePlan) -> Result<HashMap<String, i32>, IsolatedNetworkError> {
    let mut opened = Vec::new();
    for name in plan.fd_names() {
        opened.push((name.to_owned(), open_stub_ns_fd()?));
    }
    Ok(into_raw_fd_map(opened))
}

#[cfg(not(unix))]
fn open_stub_ns_fds(_plan: NamespacePlan) -> Result<HashMap<String, i32>, IsolatedNetworkError> {
    Ok(HashMap::new())
}

#[cfg(unix)]
fn open_stub_ns_fd() -> Result<File, IsolatedNetworkError> {
    let file = File::open("/dev/null").map_err(|error| IsolatedNetworkError::SetupFailed {
        step: format!("open stub namespace fd: {error}"),
    })?;
    #[cfg(target_os = "linux")]
    clear_cloexec(file.as_raw_fd())?;
    Ok(file)
}

#[cfg(target_os = "linux")]
fn namespace_fd_path(holder_pid: i32, name: &str) -> String {
    match name {
        "user" => format!("/proc/{holder_pid}/ns/user"),
        "mnt" => format!("/proc/{holder_pid}/ns/mnt"),
        "pid" => format!("/proc/{holder_pid}/ns/pid_for_children"),
        "net" => format!("/proc/{holder_pid}/ns/net"),
        _ => unreachable!("namespace plan emitted an unknown fd name"),
    }
}

#[cfg(target_os = "linux")]
fn open_inheritable_fd(path: impl AsRef<std::path::Path>) -> Result<File, IsolatedNetworkError> {
    let file = File::open(path.as_ref()).map_err(setup_error)?;
    clear_cloexec(file.as_raw_fd())?;
    Ok(file)
}

#[cfg(unix)]
fn into_raw_fd_map(opened: Vec<(String, File)>) -> HashMap<String, i32> {
    opened
        .into_iter()
        .map(|(name, file)| (name, file.into_raw_fd()))
        .collect()
}

#[cfg(target_os = "linux")]
pub(super) fn clear_cloexec(fd: RawFd) -> Result<(), IsolatedNetworkError> {
    fcntl(fd, FcntlArg::F_SETFD(FdFlag::empty())).map_err(setup_error)?;
    Ok(())
}

#[cfg(target_os = "linux")]
pub(super) fn set_nonblocking(fd: RawFd) -> Result<(), IsolatedNetworkError> {
    let flags = fcntl(fd, FcntlArg::F_GETFL).map_err(setup_error)?;
    fcntl(
        fd,
        FcntlArg::F_SETFL(OFlag::from_bits_truncate(flags) | OFlag::O_NONBLOCK),
    )
    .map_err(setup_error)?;
    Ok(())
}

#[cfg(target_os = "linux")]
pub(super) fn expect_line(
    fd: RawFd,
    prefix: &[u8],
    timeout_s: f64,
) -> Result<(), IsolatedNetworkError> {
    let deadline = Instant::now() + Duration::from_secs_f64(timeout_s.max(0.0));
    let mut buf = Vec::new();
    loop {
        if Instant::now() >= deadline {
            return Err(IsolatedNetworkError::SetupFailed {
                step: format!(
                    "ns_holder did not signal {}",
                    String::from_utf8_lossy(prefix)
                ),
            });
        }
        let mut chunk = [0_u8; 64];
        match read(fd, &mut chunk) {
            Ok(0) => {
                return Err(IsolatedNetworkError::SetupFailed {
                    step: "ns_holder closed pipe before signaling".to_owned(),
                });
            }
            Ok(read) => {
                buf.extend_from_slice(&chunk[..read]);
                if buf.contains(&b'\n') {
                    if buf.starts_with(prefix) {
                        return Ok(());
                    }
                    return Err(IsolatedNetworkError::SetupFailed {
                        step: format!("unexpected ns_holder signal: {buf:?}"),
                    });
                }
            }
            Err(Errno::EAGAIN) => thread::sleep(Duration::from_millis(10)),
            Err(Errno::EINTR) => {}
            Err(error) => return Err(setup_error(error)),
        }
    }
}

#[cfg(target_os = "linux")]
pub(super) fn write_all_fd(fd: RawFd, bytes: &[u8]) -> Result<(), IsolatedNetworkError> {
    let mut file = OpenOptions::new()
        .write(true)
        .open(format!("/proc/self/fd/{fd}"))
        .map_err(setup_error)?;
    file.write_all(bytes).map_err(setup_error)
}

#[cfg(target_os = "linux")]
pub(super) fn ns_fds_from_map(map: &HashMap<String, i32>) -> Option<NsFds> {
    (!map.is_empty()).then(|| NsFds {
        user: map.get("user").copied().map(Fd),
        mnt: map.get("mnt").copied().map(Fd),
        pid: map.get("pid").copied().map(Fd),
        net: map.get("net").copied().map(Fd),
    })
}
