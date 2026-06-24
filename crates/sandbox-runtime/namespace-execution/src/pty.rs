use std::fs::{File, OpenOptions};
use std::io::{self, Read, Write};
use std::sync::{Arc, Mutex};
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

/// Cap on how long a single `write_stdin` pushes bytes into the PTY before
/// returning a structured backpressure error. The master is non-blocking, so a
/// consumer that never drains its stdin cannot wedge the writer past this bound.
const STDIN_WRITE_DEADLINE: Duration = Duration::from_secs(2);

/// The master side of a PTY: a non-blocking stdin writer, an in-memory transcript
/// drained by a reader thread, and a cancel action. Workspace-agnostic — the
/// transcript sink is an in-memory buffer (file persistence/truncation is Phase 3).
pub struct PtyMaster {
    pgid: Option<i32>,
    writer: Mutex<File>,
    transcript: Arc<Mutex<Vec<u8>>>,
    cancel: Box<dyn Fn() + Send + Sync>,
}

impl PtyMaster {
    /// Wrap a PTY master: clone the writer, mark the OFD non-blocking, and spawn
    /// the output reader. `cancel` is the independent teardown action (killpg for
    /// the fork backing).
    pub fn spawn(
        master: File,
        pgid: Option<i32>,
        cancel: Box<dyn Fn() + Send + Sync>,
    ) -> io::Result<Self> {
        set_nonblocking(&master)?;
        let writer = master.try_clone()?;
        let transcript = Arc::new(Mutex::new(Vec::new()));
        spawn_output_reader(master, Arc::clone(&transcript));
        Ok(Self {
            pgid,
            writer: Mutex::new(writer),
            transcript,
            cancel,
        })
    }

    pub fn pgid(&self) -> Option<i32> {
        self.pgid
    }

    /// Push `bytes` to stdin without blocking unbounded. The master is
    /// non-blocking; when the consumer stops draining, this waits for writability
    /// only up to `STDIN_WRITE_DEADLINE` before a structured backpressure error.
    pub fn write_stdin(&self, bytes: &[u8]) -> io::Result<()> {
        let mut writer = self.writer.lock().expect("pty writer mutex poisoned");
        let deadline = Instant::now() + STDIN_WRITE_DEADLINE;
        let mut offset = 0;
        while offset < bytes.len() {
            match writer.write(&bytes[offset..]) {
                Ok(0) => {
                    return Err(io::Error::new(io::ErrorKind::WriteZero, "pty stdin closed"));
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

    pub fn read_output_since(&self, offset: u64) -> String {
        let transcript = self
            .transcript
            .lock()
            .expect("pty transcript mutex poisoned");
        let start = usize::try_from(offset)
            .unwrap_or(usize::MAX)
            .min(transcript.len());
        String::from_utf8_lossy(&transcript[start..]).into_owned()
    }

    pub fn output_len(&self) -> u64 {
        let transcript = self
            .transcript
            .lock()
            .expect("pty transcript mutex poisoned");
        u64::try_from(transcript.len()).unwrap_or(u64::MAX)
    }

    pub fn cancel(&self) {
        (self.cancel)();
    }
}

fn spawn_output_reader(mut master: File, transcript: Arc<Mutex<Vec<u8>>>) {
    thread::spawn(move || {
        let mut buf = [0_u8; 8192];
        loop {
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
                Ok(n) => transcript
                    .lock()
                    .expect("pty transcript mutex poisoned")
                    .extend_from_slice(&buf[..n]),
                Err(err) if err.kind() == io::ErrorKind::WouldBlock => {}
                Err(err) if err.kind() == io::ErrorKind::Interrupted => {}
                Err(_) => break,
            }
        }
    });
}

/// Open a master/slave PTY pair; runs on darwin via the `cfg(not(linux))`
/// `ptsname` branch and on linux via `ioctl_tiocgptpeer`.
pub fn open_pty_pair() -> io::Result<(File, File)> {
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

/// SIGTERM then (after a grace period) SIGKILL the process group — the cancel
/// action for the fork backing. The runner runs in its own group, so this
/// unblocks the watcher's `wait` without the watcher mediating.
pub(crate) fn terminate_process_group(pgid: i32) {
    if killpg(Pid::from_raw(pgid), Signal::SIGTERM).is_ok() {
        thread::sleep(Duration::from_millis(50));
        let _ = killpg(Pid::from_raw(pgid), Signal::SIGKILL);
    }
}

fn set_nonblocking(file: &File) -> io::Result<()> {
    let flags = fcntl_getfl(file)?;
    fcntl_setfl(file, flags | OFlags::NONBLOCK)?;
    Ok(())
}

fn poll_timeout_ms(deadline: Instant) -> i32 {
    let remaining = deadline.saturating_duration_since(Instant::now());
    i32::try_from(remaining.as_millis()).unwrap_or(i32::MAX)
}

fn stdin_backpressure() -> io::Error {
    io::Error::new(
        io::ErrorKind::WouldBlock,
        "stdin_backpressure: consumer is not draining its stdin",
    )
}
