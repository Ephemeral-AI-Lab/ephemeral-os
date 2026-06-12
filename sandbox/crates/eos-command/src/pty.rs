use std::fs::{File, OpenOptions};
use std::io::{self, Read, Write};
use std::os::unix::process::{CommandExt, ExitStatusExt};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::sync::{mpsc, Mutex, MutexGuard, PoisonError};
use std::thread;
use std::time::{Duration, Instant};

use nix::sys::signal::{killpg, Signal};
use nix::unistd::Pid;
use rustix::event::{poll, PollFd, PollFlags};
use rustix::fs::{fcntl_getfl, fcntl_setfl, OFlags};
#[cfg(target_os = "linux")]
use rustix::pty::ioctl_tiocgptpeer;
#[cfg(not(target_os = "linux"))]
use rustix::pty::ptsname;
use rustix::pty::{grantpt, openpt, unlockpt, OpenptFlags};
use serde_json::Value;

use crate::transcript::TranscriptTimestampPrefixer;

/// Cap on how long a single `write_stdin` pushes bytes into the PTY before
/// returning a structured backpressure error. The master is non-blocking, so a
/// consumer that never drains its stdin cannot wedge the writer past this bound.
const STDIN_WRITE_DEADLINE: Duration = Duration::from_secs(2);

pub(crate) struct PtyProcess {
    pgid: Option<i32>,
    writer: Mutex<File>,
    reader_done: Mutex<Option<mpsc::Receiver<()>>>,
    child: Mutex<Option<Child>>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct CommandProcessExit {
    exit_code: Option<i64>,
}

impl CommandProcessExit {
    #[must_use]
    pub fn unwaitable() -> Self {
        Self { exit_code: None }
    }

    #[must_use]
    pub fn from_status(status: ExitStatus) -> Self {
        let exit_code = status
            .code()
            .map(i64::from)
            .or_else(|| status.signal().map(|signal| -i64::from(signal)));
        Self { exit_code }
    }

    #[must_use]
    pub const fn exit_code(self) -> Option<i64> {
        self.exit_code
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum ProcessReap {
    Running,
    Exited(CommandProcessExit),
}

/// Why the substrate killed a command process group. The owning run
/// maps this to the final status — `Cancelled` → "cancelled"/130, `TimedOut` →
/// "timed_out"/124 — and either reason DISCARDS the overlay (a killed command
/// never OCC-merges).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum KillReason {
    /// A caller asked to cancel (Ctrl-C/Ctrl-D, the cancel op, or run teardown).
    Cancelled,
    /// The command exceeded its deadline and the reaper killed it as a backstop.
    TimedOut,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct CommandCompletionStatus {
    status: String,
    exit_code: i64,
}

impl CommandCompletionStatus {
    #[must_use]
    pub fn from_process_and_runner(
        process_exit: CommandProcessExit,
        runner: Option<&CommandRunnerResult>,
        kill: Option<KillReason>,
    ) -> Self {
        let mut exit_code = runner
            .map(CommandRunnerResult::exit_code)
            .or_else(|| process_exit.exit_code())
            .unwrap_or(1);
        let mut status = runner
            .and_then(CommandRunnerResult::status)
            .unwrap_or("error")
            .to_owned();
        match kill {
            Some(KillReason::Cancelled) => {
                status = "cancelled".to_owned();
                exit_code = 130;
            }
            Some(KillReason::TimedOut) => {
                status = "timed_out".to_owned();
                exit_code = 124;
            }
            None => {}
        }
        Self { status, exit_code }
    }

    #[must_use]
    pub fn status(&self) -> &str {
        &self.status
    }

    #[must_use]
    pub const fn exit_code(&self) -> i64 {
        self.exit_code
    }
}

#[derive(Debug, Clone, PartialEq)]
pub(crate) struct CommandRunnerResult {
    exit_code: i64,
    status: Option<String>,
    value: Value,
}

impl CommandRunnerResult {
    #[must_use]
    pub fn read_from_path(path: &Path) -> Option<Self> {
        let bytes = std::fs::read(path).ok()?;
        let value = serde_json::from_slice::<Value>(&bytes).ok()?;
        Self::from_value(value)
    }

    #[must_use]
    pub fn from_value(value: Value) -> Option<Self> {
        let exit_code = value.get("exit_code").and_then(|value| {
            value
                .as_i64()
                .or_else(|| value.as_u64().and_then(|value| i64::try_from(value).ok()))
        })?;
        let status = value
            .get("payload")
            .and_then(Value::as_object)
            .and_then(|payload| payload.get("status"))
            .and_then(Value::as_str)
            .map(str::to_owned);
        Some(Self {
            exit_code,
            status,
            value,
        })
    }

    #[must_use]
    pub const fn exit_code(&self) -> i64 {
        self.exit_code
    }

    #[must_use]
    pub fn status(&self) -> Option<&str> {
        self.status.as_deref()
    }

    #[must_use]
    pub const fn value(&self) -> &Value {
        &self.value
    }
}

impl PtyProcess {
    /// Process-free scaffold backing [`crate::process::CommandProcess::new`].
    #[must_use]
    pub(crate) fn inactive(writer: File) -> Self {
        Self {
            pgid: None,
            writer: Mutex::new(writer),
            reader_done: Mutex::new(None),
            child: Mutex::new(None),
        }
    }

    /// Push `bytes` to the command's stdin without blocking unbounded. The master
    /// is non-blocking; when the consumer stops draining, `write` returns
    /// `WouldBlock` and we wait for writability only up to `STDIN_WRITE_DEADLINE`
    /// before returning a structured backpressure error. Cancel/terminate is a
    /// separate (`killpg`) path, so the command stays controllable throughout.
    pub(crate) fn write_stdin(&self, bytes: &[u8]) -> io::Result<()> {
        let mut writer = lock(&self.writer);
        let deadline = Instant::now() + STDIN_WRITE_DEADLINE;
        let mut offset = 0;
        while offset < bytes.len() {
            match writer.write(&bytes[offset..]) {
                Ok(0) => {
                    return Err(io::Error::new(
                        io::ErrorKind::WriteZero,
                        "command stdin closed",
                    ));
                }
                Ok(written) => offset += written,
                Err(err) if err.kind() == io::ErrorKind::Interrupted => {}
                Err(err) if err.kind() == io::ErrorKind::WouldBlock => {
                    let timeout_ms = poll_timeout_ms(deadline);
                    if timeout_ms == 0 {
                        return Err(stdin_backpressure());
                    }
                    let mut fds = [PollFd::new(&*writer, PollFlags::OUT)];
                    match poll(&mut fds, timeout_ms) {
                        Ok(0) => return Err(stdin_backpressure()),
                        Ok(_) => {}
                        Err(rustix::io::Errno::INTR) => {}
                        Err(err) => return Err(io::Error::from(err)),
                    }
                }
                Err(err) => return Err(err),
            }
        }
        Ok(())
    }

    pub(crate) fn terminate(&self) {
        if let Some(pgid) = self.pgid {
            terminate_process_group(pgid);
        }
    }

    #[must_use]
    pub(crate) fn try_reap(&self) -> ProcessReap {
        let mut child = lock(&self.child);
        match child.as_mut() {
            Some(handle) => match handle.try_wait() {
                Ok(Some(status)) => {
                    let _ = child.take();
                    ProcessReap::Exited(CommandProcessExit::from_status(status))
                }
                Ok(None) => ProcessReap::Running,
                Err(_) => {
                    let _ = child.take();
                    ProcessReap::Exited(CommandProcessExit::unwaitable())
                }
            },
            None => ProcessReap::Exited(CommandProcessExit::unwaitable()),
        }
    }

    pub(crate) fn wait_for_reader_done(&self, timeout: Duration) {
        let reader_done = lock(&self.reader_done).take();
        if let Some(reader_done) = reader_done {
            let _ = reader_done.recv_timeout(timeout);
        }
    }
}

pub(crate) fn spawn_current_exe_ns_runner(
    request_path: &Path,
    run_request: &Value,
    output_path: &Path,
    transcript_path: PathBuf,
    transcript_timestamp_timezone: &str,
) -> io::Result<PtyProcess> {
    write_run_request(request_path, run_request)?;
    let (master, slave) = open_pty_pair()?;
    // Non-blocking master OFD (shared by the writer dup and the reader): writes
    // can't wedge on a non-draining consumer, and the reader polls instead.
    set_nonblocking(&master)?;
    let mut child_command = Command::new(std::env::current_exe()?);
    child_command
        .arg("ns-runner")
        .arg("--request")
        .arg(request_path)
        .arg("--output")
        .arg(output_path)
        .stdin(Stdio::from(slave.try_clone()?))
        .stdout(Stdio::from(slave.try_clone()?))
        .stderr(Stdio::from(slave))
        .process_group(0);
    let child = child_command.spawn()?;
    let pgid = i32::try_from(child.id()).map_err(|_| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!("child pid does not fit i32: {}", child.id()),
        )
    })?;
    let writer = master.try_clone()?;
    let transcript_prefixer = TranscriptTimestampPrefixer::new(transcript_timestamp_timezone)
        .map_err(|error| {
            io::Error::new(
                io::ErrorKind::InvalidInput,
                format!("invalid transcript timestamp timezone: {error}"),
            )
        })?;
    let reader_done = spawn_command_output_reader(master, transcript_path, transcript_prefixer);

    Ok(PtyProcess {
        pgid: Some(pgid),
        writer: Mutex::new(writer),
        reader_done: Mutex::new(Some(reader_done)),
        child: Mutex::new(Some(child)),
    })
}

fn spawn_command_output_reader(
    mut master: File,
    transcript_path: PathBuf,
    mut transcript_prefixer: TranscriptTimestampPrefixer,
) -> mpsc::Receiver<()> {
    let (done_tx, done_rx) = mpsc::channel();
    thread::spawn(move || {
        let mut transcript = OpenOptions::new()
            .create(true)
            .append(true)
            .open(transcript_path)
            .ok();
        let mut buf = [0_u8; 8192];
        loop {
            // The master is non-blocking; block here until readable or hangup with
            // no busy-loop and no added latency (infinite poll wakes on the first
            // byte or on slave close), then drain what is available.
            {
                let mut fds = [PollFd::new(&master, PollFlags::IN)];
                match poll(&mut fds, -1) {
                    Ok(_) => {}
                    Err(rustix::io::Errno::INTR) => continue,
                    Err(_) => break,
                }
            }
            match master.read(&mut buf) {
                Ok(0) => break,
                Ok(n) => {
                    let transcript_bytes = transcript_prefixer.prefix(&buf[..n]);
                    if let Some(file) = transcript.as_mut() {
                        if file.write_all(&transcript_bytes).is_err() {
                            transcript = None;
                        }
                    }
                }
                Err(err) if err.kind() == io::ErrorKind::WouldBlock => {}
                Err(err) if err.kind() == io::ErrorKind::Interrupted => {}
                Err(_) => break,
            }
        }
        let _ = done_tx.send(());
    });
    done_rx
}

fn write_run_request(path: &Path, request: &Value) -> io::Result<()> {
    let bytes = serde_json::to_vec(request).map_err(|error| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!("serialize command runner request: {error}"),
        )
    })?;
    std::fs::write(path, bytes)
}

fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(PoisonError::into_inner)
}

fn open_pty_pair() -> io::Result<(File, File)> {
    let flags = OpenptFlags::RDWR | OpenptFlags::NOCTTY;
    #[cfg(target_os = "linux")]
    let flags = flags | OpenptFlags::CLOEXEC;
    let master = openpt(flags).map_err(io::Error::from)?;
    grantpt(&master).map_err(io::Error::from)?;
    unlockpt(&master).map_err(io::Error::from)?;

    #[cfg(target_os = "linux")]
    let slave = File::from(ioctl_tiocgptpeer(&master, flags).map_err(io::Error::from)?);
    #[cfg(not(target_os = "linux"))]
    let slave = {
        let slave_name = ptsname(&master, Vec::new()).map_err(io::Error::from)?;
        OpenOptions::new()
            .read(true)
            .write(true)
            .open(slave_name.to_string_lossy().as_ref())?
    };

    Ok((File::from(master), slave))
}

fn terminate_process_group(pgid: i32) {
    if killpg(Pid::from_raw(pgid), Signal::SIGTERM).is_ok() {
        thread::sleep(Duration::from_millis(50));
        let _ = killpg(Pid::from_raw(pgid), Signal::SIGKILL);
    }
}

/// Mark `file`'s open file description non-blocking so `read`/`write` return
/// `WouldBlock` instead of stalling. Applied to the PTY master before it is
/// shared between the writer dup and the reader thread.
fn set_nonblocking(file: &File) -> io::Result<()> {
    let flags = fcntl_getfl(file)?;
    fcntl_setfl(file, flags | OFlags::NONBLOCK)?;
    Ok(())
}

/// Milliseconds left until `deadline`, clamped to a non-negative `i32` for
/// `poll`. Returns 0 once the deadline has passed.
fn poll_timeout_ms(deadline: Instant) -> i32 {
    let remaining = deadline.saturating_duration_since(Instant::now());
    i32::try_from(remaining.as_millis()).unwrap_or(i32::MAX)
}

fn stdin_backpressure() -> io::Error {
    io::Error::new(
        io::ErrorKind::WouldBlock,
        "stdin_backpressure: command is not draining its stdin",
    )
}

#[cfg(test)]
#[path = "../tests/unit/pty.rs"]
mod tests;
