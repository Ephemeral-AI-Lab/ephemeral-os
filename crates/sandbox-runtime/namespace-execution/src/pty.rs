use std::fs::{File, OpenOptions};
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
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

use crate::transcript::TranscriptTimestampPrefixer;

/// Cap on how long a single `write_stdin` pushes bytes into the PTY before
/// returning a structured backpressure error. The master is non-blocking, so a
/// consumer that never drains its stdin cannot wedge the writer past this bound.
const STDIN_WRITE_DEADLINE: Duration = Duration::from_secs(2);

/// Cap on a single file-backed `read_output_since` read window.
const MAX_OUTPUT_READ_BYTES: u64 = 1024 * 1024;

/// Where the PTY reader drains output. A `File` sink persists timestamp-prefixed
/// bytes for the command's file-backed transcript (the row reader lives in the
/// `command` crate); a `Memory` sink keeps the Phase-2 in-memory buffer for ops
/// that do not need persistence.
enum TranscriptSink {
    Memory(Arc<Mutex<Vec<u8>>>),
    File(PathBuf),
}

/// The master side of a PTY: a non-blocking stdin writer, a transcript drained by
/// a reader thread (in-memory or file-backed), and a cancel action.
pub struct PtyMaster {
    pgid: Option<i32>,
    writer: Mutex<File>,
    sink: TranscriptSink,
    cancel: Arc<dyn Fn() + Send + Sync>,
}

impl PtyMaster {
    /// Wrap a PTY master: clone the writer, mark the OFD non-blocking, and spawn
    /// the output reader. `transcript_path` selects the sink (file vs in-memory);
    /// `cancel` is the independent teardown action (killpg for the fork backing).
    pub fn spawn(
        master: File,
        pgid: Option<i32>,
        transcript_path: Option<PathBuf>,
        cancel: Box<dyn Fn() + Send + Sync>,
    ) -> io::Result<Self> {
        set_nonblocking(&master)?;
        let writer = master.try_clone()?;
        let sink = match transcript_path {
            Some(path) => {
                spawn_file_output_reader(master, &path);
                TranscriptSink::File(path)
            }
            None => {
                let transcript = Arc::new(Mutex::new(Vec::new()));
                let reader_transcript = Arc::clone(&transcript);
                spawn_output_reader(master, move |bytes| {
                    reader_transcript
                        .lock()
                        .expect("pty transcript mutex poisoned")
                        .extend_from_slice(bytes);
                });
                TranscriptSink::Memory(transcript)
            }
        };
        Ok(Self {
            pgid,
            writer: Mutex::new(writer),
            sink,
            cancel: Arc::from(cancel),
        })
    }

    pub fn pgid(&self) -> Option<i32> {
        self.pgid
    }

    /// A cloneable cancel action, so a caller can release the registry lock
    /// before invoking it (`terminate_process_group` blocks for the SIGTERM grace
    /// period, which must not be held under the registry lock).
    pub fn cancel_handle(&self) -> Arc<dyn Fn() + Send + Sync> {
        Arc::clone(&self.cancel)
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
        match &self.sink {
            TranscriptSink::Memory(transcript) => {
                let transcript = transcript.lock().expect("pty transcript mutex poisoned");
                let start = usize::try_from(offset)
                    .unwrap_or(usize::MAX)
                    .min(transcript.len());
                String::from_utf8_lossy(&transcript[start..]).into_owned()
            }
            TranscriptSink::File(path) => read_file_since(path, offset),
        }
    }

    pub fn output_len(&self) -> u64 {
        match &self.sink {
            TranscriptSink::Memory(transcript) => {
                let transcript = transcript.lock().expect("pty transcript mutex poisoned");
                u64::try_from(transcript.len()).unwrap_or(u64::MAX)
            }
            TranscriptSink::File(path) => {
                std::fs::metadata(path).map_or(0, |metadata| metadata.len())
            }
        }
    }

    pub fn cancel(&self) {
        (self.cancel)();
    }
}

fn spawn_file_output_reader(master: File, transcript_path: &Path) {
    let mut transcript = OpenOptions::new()
        .create(true)
        .append(true)
        .open(transcript_path)
        .ok();
    let mut prefixer = TranscriptTimestampPrefixer::new();
    spawn_output_reader(master, move |bytes| {
        let prefixed = prefixer.prefix(bytes);
        if transcript
            .as_mut()
            .is_some_and(|file| file.write_all(&prefixed).is_err())
        {
            transcript = None;
        }
    });
}

fn spawn_output_reader(mut master: File, mut sink: impl FnMut(&[u8]) + Send + 'static) {
    thread::spawn(move || {
        let mut buf = [0_u8; 8192];
        while poll_readable(&master) {
            match master.read(&mut buf) {
                Ok(0) => break,
                Ok(n) => sink(&buf[..n]),
                Err(err) if err.kind() == io::ErrorKind::WouldBlock => {}
                Err(err) if err.kind() == io::ErrorKind::Interrupted => {}
                Err(_) => break,
            }
        }
    });
}

fn poll_readable(master: &File) -> bool {
    loop {
        let mut fds = [PollFd::new(master, PollFlags::IN)];
        match poll(&mut fds, -1) {
            Ok(_) => return true,
            Err(rustix::io::Errno::INTR) => continue,
            Err(_) => return false,
        }
    }
}

fn read_file_since(path: &Path, offset: u64) -> String {
    let Ok(mut file) = File::open(path) else {
        return String::new();
    };
    let Ok(metadata) = file.metadata() else {
        return String::new();
    };
    let len = metadata.len();
    let start = offset.min(len);
    let bounded_start = start.max(len.saturating_sub(MAX_OUTPUT_READ_BYTES));
    if file.seek(SeekFrom::Start(bounded_start)).is_err() {
        return String::new();
    }
    let mut bytes = Vec::new();
    if file
        .take(MAX_OUTPUT_READ_BYTES)
        .read_to_end(&mut bytes)
        .is_err()
    {
        return String::new();
    }
    String::from_utf8_lossy(&bytes).into_owned()
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
