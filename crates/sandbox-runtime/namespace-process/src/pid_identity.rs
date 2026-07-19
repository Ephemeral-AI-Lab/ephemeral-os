#[cfg(target_os = "linux")]
use rustix::process::{pidfd_open, Pid, PidfdFlags};
#[cfg(target_os = "linux")]
use std::os::fd::OwnedFd;

#[cfg(target_os = "linux")]
pub type PidIdentityGuard = OwnedFd;

#[cfg(not(target_os = "linux"))]
pub struct PidIdentityGuard;

#[cfg(target_os = "linux")]
pub fn pin_pid_identity(pid: u32) -> Result<PidIdentityGuard, String> {
    let raw_pid = i32::try_from(pid).map_err(|_| format!("PID {pid} exceeds i32"))?;
    let rustix_pid = Pid::from_raw(raw_pid).ok_or_else(|| format!("PID {pid} is invalid"))?;
    pidfd_open(rustix_pid, PidfdFlags::empty())
        .map_err(|error| format!("pidfd_open({pid}): {error}"))
}

#[cfg(not(target_os = "linux"))]
pub fn pin_pid_identity(_pid: u32) -> Result<PidIdentityGuard, String> {
    Err("pidfd identity pinning is unavailable on this platform".to_owned())
}
