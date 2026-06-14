use std::collections::HashMap;
#[cfg(target_os = "linux")]
use std::fs::{File, OpenOptions};
#[cfg(target_os = "linux")]
use std::io::{Read, Write};
#[cfg(target_os = "linux")]
use std::os::fd::{AsRawFd, IntoRawFd, RawFd};
#[cfg(target_os = "linux")]
use std::os::unix::process::CommandExt;
#[cfg(all(target_os = "linux", unix))]
use std::os::unix::process::ExitStatusExt;
use std::path::PathBuf;
#[cfg(target_os = "linux")]
use std::process::{Child, ExitStatus, Output, Stdio};
#[cfg(test)]
use std::sync::Arc;
#[cfg(target_os = "linux")]
use std::sync::{Mutex, MutexGuard, OnceLock};
#[cfg(target_os = "linux")]
use std::thread;
#[cfg(target_os = "linux")]
use std::time::{Duration, Instant};

#[cfg(target_os = "linux")]
use namespace::protocol::{
    Fd, NsFds, RunMode, RunRequest, RunResult, RunnerVerb, ToolCall, WorkspaceRoot,
};
#[cfg(target_os = "linux")]
use nix::errno::Errno;
#[cfg(target_os = "linux")]
use nix::fcntl::{fcntl, FcntlArg, FdFlag, OFlag};
#[cfg(target_os = "linux")]
use nix::sys::signal::{kill, Signal};
#[cfg(target_os = "linux")]
use nix::unistd::{close, pipe2, read, Pid};
#[cfg(target_os = "linux")]
use serde_json::{json, Value};

use crate::isolated_workspace::error::IsolatedError;
use crate::isolated_workspace::manager::{DnsConfiguration, WorkspaceHandle};

pub(crate) const TEST_HARNESS_ENV: &str = "EOS_ISOLATED_WORKSPACE_TEST_HARNESS";

pub(crate) fn setup_error(error: impl std::fmt::Display) -> IsolatedError {
    IsolatedError::SetupFailed {
        step: error.to_string(),
    }
}

pub(crate) fn test_harness_enabled() -> bool {
    std::env::var(TEST_HARNESS_ENV)
        .is_ok_and(|value| matches!(value.trim(), "1" | "true" | "TRUE" | "yes" | "YES"))
}

pub(crate) struct NamespaceRuntime {
    stub: bool,
    #[cfg(test)]
    stub_holder_pid: i32,
    #[cfg(test)]
    killed_holders: Option<Arc<std::sync::Mutex<Vec<i32>>>>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub(crate) struct HolderKillReport {
    pub(crate) holder_was_alive: bool,
    pub(crate) exit_status: Option<i32>,
    pub(crate) signal: Option<i32>,
    pub(crate) status_raw: Option<i32>,
}

impl NamespaceRuntime {
    pub(crate) fn from_env() -> Self {
        Self {
            stub: test_harness_enabled(),
            #[cfg(test)]
            stub_holder_pid: 0,
            #[cfg(test)]
            killed_holders: None,
        }
    }

    pub(crate) fn stubbed() -> Self {
        Self {
            stub: true,
            #[cfg(test)]
            stub_holder_pid: 0,
            #[cfg(test)]
            killed_holders: None,
        }
    }

    #[cfg(test)]
    pub(crate) fn stubbed_with_holder(
        pid: i32,
        killed_holders: Arc<std::sync::Mutex<Vec<i32>>>,
    ) -> Self {
        Self {
            stub: true,
            stub_holder_pid: pid,
            killed_holders: Some(killed_holders),
        }
    }

    pub(crate) fn spawn_ns_holder(
        &self,
        handle: &mut WorkspaceHandle,
        setup_timeout_s: f64,
    ) -> Result<i32, IsolatedError> {
        if self.stub {
            #[cfg(test)]
            {
                let _ = (handle, setup_timeout_s);
                return Ok(self.stub_holder_pid);
            }
            #[cfg(not(test))]
            return Ok(0);
        }
        #[cfg(not(target_os = "linux"))]
        {
            let _ = (handle, setup_timeout_s);
            Ok(0)
        }
        #[cfg(target_os = "linux")]
        {
            let (readiness_read, readiness_write) = pipe2(OFlag::O_CLOEXEC).map_err(setup_error)?;
            let (control_read, control_write) = pipe2(OFlag::O_CLOEXEC).map_err(setup_error)?;
            let readiness_child_fd = readiness_write.as_raw_fd();
            let control_child_fd = control_read.as_raw_fd();
            clear_cloexec(readiness_child_fd)?;
            clear_cloexec(control_child_fd)?;
            let mut child = Command::new(std::env::current_exe().map_err(setup_error)?)
                .arg("ns-holder")
                .arg(readiness_child_fd.to_string())
                .arg(control_child_fd.to_string())
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn()
                .map_err(setup_error)?;
            drop(readiness_write);
            drop(control_read);
            let readiness_fd = readiness_read.into_raw_fd();
            let control_fd = control_write.into_raw_fd();
            handle.readiness_fd = readiness_fd;
            handle.control_fd = control_fd;
            if let Err(error) = set_nonblocking(readiness_fd)
                .and_then(|()| expect_line(readiness_fd, b"ns-up", setup_timeout_s))
            {
                let _ = child.kill();
                let _ = child.wait();
                let _ = close(readiness_fd);
                let _ = close(control_fd);
                return Err(error);
            }
            let Ok(holder_pid) = i32::try_from(child.id()) else {
                let _ = child.kill();
                let _ = child.wait();
                let _ = close(readiness_fd);
                let _ = close(control_fd);
                return Err(setup_error(format!(
                    "ns-holder pid does not fit i32: {}",
                    child.id()
                )));
            };
            lock_holder_children()?.insert(holder_pid, child);
            Ok(holder_pid)
        }
    }

    pub(crate) fn open_ns_fds(
        &self,
        holder_pid: i32,
    ) -> Result<HashMap<String, i32>, IsolatedError> {
        if self.stub || holder_pid <= 0 {
            return Ok(HashMap::new());
        }
        #[cfg(not(target_os = "linux"))]
        {
            let _ = holder_pid;
            Ok(HashMap::new())
        }
        #[cfg(target_os = "linux")]
        {
            let paths = [
                ("user", format!("/proc/{holder_pid}/ns/user")),
                ("mnt", format!("/proc/{holder_pid}/ns/mnt")),
                ("pid", format!("/proc/{holder_pid}/ns/pid_for_children")),
                ("net", format!("/proc/{holder_pid}/ns/net")),
            ];
            paths
                .into_iter()
                .map(|(name, path)| Ok((name.to_owned(), open_inheritable_fd(path)?)))
                .collect()
        }
    }

    pub(crate) fn mount_overlay(
        &self,
        handle: &WorkspaceHandle,
        layer_paths: &[PathBuf],
        setup_timeout_s: f64,
    ) -> Result<(), IsolatedError> {
        if self.stub || handle.holder_pid <= 0 {
            return Ok(());
        }
        #[cfg(not(target_os = "linux"))]
        {
            let _ = (handle, layer_paths, setup_timeout_s);
        }
        #[cfg(target_os = "linux")]
        {
            let request = ns_runner_request(
                handle,
                "mount",
                "setns_overlay_mount",
                json!({}),
                layer_paths.to_vec(),
            );
            run_ns_runner_mount_overlay_child(&request, setup_timeout_s)?;
        }
        Ok(())
    }

    pub(crate) fn configure_dns(
        &self,
        handle: &WorkspaceHandle,
        fallback_dns: &str,
        setup_timeout_s: f64,
    ) -> Result<DnsConfiguration, IsolatedError> {
        if self.stub || handle.holder_pid <= 0 {
            return Ok(DnsConfiguration::default());
        }
        #[cfg(not(target_os = "linux"))]
        {
            let _ = (handle, fallback_dns, setup_timeout_s);
            Ok(DnsConfiguration::default())
        }
        #[cfg(target_os = "linux")]
        {
            let request = ns_runner_request(
                handle,
                "configure-dns",
                "configure_dns",
                json!({"fallback_dns": fallback_dns}),
                Vec::new(),
            );
            run_ns_runner_configure_dns_child(&request, setup_timeout_s)
        }
    }

    pub(crate) fn signal_net_ready(
        &self,
        handle: &WorkspaceHandle,
        setup_timeout_s: f64,
    ) -> Result<(), IsolatedError> {
        if self.stub || handle.holder_pid <= 0 {
            return Ok(());
        }
        #[cfg(not(target_os = "linux"))]
        {
            let _ = (handle, setup_timeout_s);
        }
        #[cfg(target_os = "linux")]
        {
            let payload = handle.veth.as_ref().map_or_else(
                || "net-ready\n".to_owned(),
                |veth| {
                    format!(
                        "net-ready {} {} {} {}\n",
                        veth.ns_name,
                        veth.ns_ip,
                        crate::isolated_workspace::network::BRIDGE_PREFIX_LEN,
                        crate::isolated_workspace::network::GATEWAY
                    )
                },
            );
            write_all_fd(handle.control_fd, payload.as_bytes())?;
            expect_line(handle.readiness_fd, b"ready", setup_timeout_s)?;
        }
        Ok(())
    }

    pub(crate) fn create_cgroup(&self, handle: &WorkspaceHandle) -> Result<PathBuf, IsolatedError> {
        if self.stub {
            return Ok(PathBuf::new());
        }
        let path = PathBuf::from(crate::isolated_workspace::caps::CGROUP_ROOT).join(format!(
            "{}{}",
            crate::isolated_workspace::caps::HANDLE_PREFIX,
            handle.workspace_id.0
        ));
        std::fs::create_dir_all(&path).map_err(setup_error)?;
        Ok(path)
    }

    pub(crate) fn kill_holder(
        &self,
        holder_pid: i32,
        grace_s: f64,
    ) -> Result<HolderKillReport, IsolatedError> {
        if self.stub || holder_pid <= 0 {
            #[cfg(test)]
            if self.stub && holder_pid > 0 {
                if let Some(killed_holders) = self.killed_holders.as_ref() {
                    killed_holders
                        .lock()
                        .map_err(|_| setup_error("stub holder kill log lock poisoned"))?
                        .push(holder_pid);
                }
            }
            return Ok(HolderKillReport::default());
        }
        #[cfg(not(target_os = "linux"))]
        {
            let _ = grace_s;
            Ok(HolderKillReport::default())
        }
        #[cfg(target_os = "linux")]
        {
            let child = lock_holder_children()?.remove(&holder_pid);
            if let Some(mut child) = child {
                if let Some(status) = child.try_wait().map_err(setup_error)? {
                    return Ok(holder_kill_report(false, status));
                }
                let _ = kill(Pid::from_raw(holder_pid), Signal::SIGTERM);
                let deadline = Instant::now() + Duration::from_secs_f64(grace_s.max(0.0));
                while Instant::now() < deadline {
                    if let Some(status) = child.try_wait().map_err(setup_error)? {
                        return Ok(holder_kill_report(true, status));
                    }
                    thread::sleep(Duration::from_millis(10));
                }
                let _ = kill(Pid::from_raw(holder_pid), Signal::SIGKILL);
                let status = child.wait().map_err(setup_error)?;
                return Ok(holder_kill_report(true, status));
            } else {
                let holder_was_alive = kill(Pid::from_raw(holder_pid), Signal::SIGTERM).is_ok();
                if holder_was_alive {
                    thread::sleep(Duration::from_secs_f64(grace_s.max(0.0)));
                    let _ = kill(Pid::from_raw(holder_pid), Signal::SIGKILL);
                }
                return Ok(HolderKillReport {
                    holder_was_alive,
                    ..HolderKillReport::default()
                });
            }
        }
    }
}

#[cfg(all(target_os = "linux", unix))]
fn holder_kill_report(
    holder_was_alive: bool,
    status: std::process::ExitStatus,
) -> HolderKillReport {
    HolderKillReport {
        holder_was_alive,
        exit_status: status.code(),
        signal: status.signal(),
        status_raw: Some(status.into_raw()),
    }
}

#[cfg(target_os = "linux")]
fn ns_runner_request(
    handle: &WorkspaceHandle,
    invocation: &str,
    verb: &str,
    args: serde_json::Value,
    layer_paths: Vec<PathBuf>,
) -> RunRequest {
    RunRequest {
        mode: RunMode::SetNs,
        tool_call: ToolCall {
            invocation_id: format!("isolated-{invocation}-{}", handle.workspace_id.0),
            caller_id: handle.caller_id.clone(),
            verb: RunnerVerb::from(verb),
            args,
            background: false,
        },
        workspace_root: WorkspaceRoot(PathBuf::from(&handle.workspace_root)),
        layer_paths,
        upperdir: Some(handle.dirs.upperdir.clone()),
        workdir: Some(handle.dirs.workdir.clone()),
        ns_fds: ns_fds_from_map(&handle.ns_fds),
        cgroup_path: handle.cgroup_path.clone(),
        timeout_seconds: None,
    }
}

#[cfg(target_os = "linux")]
fn holder_children() -> &'static Mutex<HashMap<i32, Child>> {
    static CHILDREN: OnceLock<Mutex<HashMap<i32, Child>>> = OnceLock::new();
    CHILDREN.get_or_init(|| Mutex::new(HashMap::new()))
}

#[cfg(target_os = "linux")]
fn lock_holder_children() -> Result<MutexGuard<'static, HashMap<i32, Child>>, IsolatedError> {
    holder_children()
        .lock()
        .map_err(|_| setup_error("ns-holder child registry lock poisoned"))
}

#[cfg(target_os = "linux")]
fn open_inheritable_fd(path: impl AsRef<std::path::Path>) -> Result<RawFd, IsolatedError> {
    let file = File::open(path.as_ref()).map_err(setup_error)?;
    clear_cloexec(file.as_raw_fd())?;
    Ok(file.into_raw_fd())
}

#[cfg(target_os = "linux")]
fn clear_cloexec(fd: RawFd) -> Result<(), IsolatedError> {
    fcntl(fd, FcntlArg::F_SETFD(FdFlag::empty())).map_err(setup_error)?;
    Ok(())
}

#[cfg(target_os = "linux")]
fn set_nonblocking(fd: RawFd) -> Result<(), IsolatedError> {
    let flags = fcntl(fd, FcntlArg::F_GETFL).map_err(setup_error)?;
    fcntl(
        fd,
        FcntlArg::F_SETFL(OFlag::from_bits_truncate(flags) | OFlag::O_NONBLOCK),
    )
    .map_err(setup_error)?;
    Ok(())
}

#[cfg(target_os = "linux")]
fn expect_line(fd: RawFd, prefix: &[u8], timeout_s: f64) -> Result<(), IsolatedError> {
    let deadline = Instant::now() + Duration::from_secs_f64(timeout_s.max(0.0));
    let mut buf = Vec::new();
    loop {
        if Instant::now() >= deadline {
            return Err(IsolatedError::SetupFailed {
                step: format!(
                    "ns_holder did not signal {}",
                    String::from_utf8_lossy(prefix)
                ),
            });
        }
        let mut chunk = [0_u8; 64];
        match read(fd, &mut chunk) {
            Ok(0) => {
                return Err(IsolatedError::SetupFailed {
                    step: "ns_holder closed pipe before signaling".to_owned(),
                });
            }
            Ok(read) => {
                buf.extend_from_slice(&chunk[..read]);
                if buf.contains(&b'\n') {
                    if buf.starts_with(prefix) {
                        return Ok(());
                    }
                    return Err(IsolatedError::SetupFailed {
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
fn write_all_fd(fd: RawFd, bytes: &[u8]) -> Result<(), IsolatedError> {
    let mut file = OpenOptions::new()
        .write(true)
        .open(format!("/proc/self/fd/{fd}"))
        .map_err(setup_error)?;
    file.write_all(bytes).map_err(setup_error)
}

#[cfg(target_os = "linux")]
fn ns_fds_from_map(map: &HashMap<String, i32>) -> Option<NsFds> {
    (!map.is_empty()).then(|| NsFds {
        user: map.get("user").copied().map(Fd),
        mnt: map.get("mnt").copied().map(Fd),
        pid: map.get("pid").copied().map(Fd),
        net: map.get("net").copied().map(Fd),
    })
}

#[cfg(target_os = "linux")]
fn run_ns_runner_mount_overlay_child(
    request: &RunRequest,
    setup_timeout_s: f64,
) -> Result<(), IsolatedError> {
    let output = run_ns_runner_child(request, "--mount-overlay", Stdio::null(), setup_timeout_s)?;
    if output.status.success() {
        return Ok(());
    }
    Err(IsolatedError::SetupFailed {
        step: format!(
            "ns-runner mount overlay failed with status {}: {}",
            output.status,
            String::from_utf8_lossy(&output.stderr)
        ),
    })
}

#[cfg(target_os = "linux")]
fn run_ns_runner_configure_dns_child(
    request: &RunRequest,
    setup_timeout_s: f64,
) -> Result<DnsConfiguration, IsolatedError> {
    let output = run_ns_runner_child(request, "--configure-dns", Stdio::piped(), setup_timeout_s)?;
    if !output.status.success() {
        return Err(IsolatedError::SetupFailed {
            step: format!(
                "ns-runner configure dns failed with status {}: {}",
                output.status,
                String::from_utf8_lossy(&output.stderr)
            ),
        });
    }
    let result = serde_json::from_slice::<RunResult>(&output.stdout).map_err(|err| {
        IsolatedError::SetupFailed {
            step: format!("invalid ns-runner configure dns output: {err}"),
        }
    })?;
    Ok(DnsConfiguration {
        fallback_applied: result
            .payload
            .get("applied_fallback")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        previous_first_nameserver: result
            .payload
            .get("previous_first_nameserver")
            .and_then(Value::as_str)
            .map(str::to_owned),
    })
}

#[cfg(target_os = "linux")]
fn run_ns_runner_child(
    request: &RunRequest,
    mode_arg: &str,
    stdout: Stdio,
    setup_timeout_s: f64,
) -> Result<Output, IsolatedError> {
    let payload = serde_json::to_vec(request).map_err(setup_error)?;
    let mut child = Command::new(std::env::current_exe().map_err(setup_error)?)
        .arg("ns-runner")
        .arg(mode_arg)
        .stdin(Stdio::piped())
        .stdout(stdout)
        .stderr(Stdio::piped())
        .process_group(0)
        .spawn()
        .map_err(setup_error)?;
    child
        .stdin
        .as_mut()
        .ok_or_else(|| IsolatedError::SetupFailed {
            step: "ns-runner stdin unavailable".to_owned(),
        })?
        .write_all(&payload)
        .map_err(setup_error)?;
    drop(child.stdin.take());
    let status = wait_for_ns_runner_child(&mut child, mode_arg, setup_timeout_s)?;
    let stdout = read_child_pipe(child.stdout.take())?;
    let stderr = read_child_pipe(child.stderr.take())?;
    Ok(Output {
        status,
        stdout,
        stderr,
    })
}

#[cfg(target_os = "linux")]
fn wait_for_ns_runner_child(
    child: &mut Child,
    mode_arg: &str,
    setup_timeout_s: f64,
) -> Result<ExitStatus, IsolatedError> {
    let deadline = Instant::now() + Duration::from_secs_f64(setup_timeout_s.max(0.0));
    loop {
        if let Some(status) = child.try_wait().map_err(setup_error)? {
            return Ok(status);
        }
        if Instant::now() >= deadline {
            terminate_ns_runner_child(child, Signal::SIGTERM);
            let grace_deadline = Instant::now() + Duration::from_millis(100);
            while Instant::now() < grace_deadline {
                if let Some(status) = child.try_wait().map_err(setup_error)? {
                    let _ = status;
                    return Err(IsolatedError::SetupFailed {
                        step: format!("ns-runner {mode_arg} timed out"),
                    });
                }
                thread::sleep(Duration::from_millis(10));
            }
            terminate_ns_runner_child(child, Signal::SIGKILL);
            let _ = child.wait();
            return Err(IsolatedError::SetupFailed {
                step: format!("ns-runner {mode_arg} timed out"),
            });
        }
        thread::sleep(Duration::from_millis(10));
    }
}

#[cfg(target_os = "linux")]
fn terminate_ns_runner_child(child: &mut Child, signal: Signal) {
    let Ok(pid) = i32::try_from(child.id()) else {
        let _ = child.kill();
        return;
    };
    let _ = kill(Pid::from_raw(-pid), signal);
    let _ = kill(Pid::from_raw(pid), signal);
}

#[cfg(target_os = "linux")]
fn read_child_pipe<R: Read>(pipe: Option<R>) -> Result<Vec<u8>, IsolatedError> {
    let Some(mut pipe) = pipe else {
        return Ok(Vec::new());
    };
    let mut bytes = Vec::new();
    pipe.read_to_end(&mut bytes).map_err(setup_error)?;
    Ok(bytes)
}

#[cfg(all(test, target_os = "linux"))]
mod tests {
    use super::*;

    #[test]
    fn ns_runner_wait_times_out_and_reaps_child_group() -> Result<(), Box<dyn std::error::Error>> {
        let mut child = Command::new("sh")
            .arg("-c")
            .arg("sleep 60")
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .process_group(0)
            .spawn()?;

        let error = wait_for_ns_runner_child(&mut child, "--test-timeout", 0.01)
            .expect_err("sleeping child should time out");

        assert!(error.to_string().contains("timed out"));
        assert!(
            child.try_wait()?.is_some(),
            "timed out child should be reaped"
        );
        Ok(())
    }
}
