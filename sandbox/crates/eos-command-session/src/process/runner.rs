use std::fs::{File, OpenOptions};
use std::io::{self, Read, Write};
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::sync::{mpsc, Mutex, MutexGuard, PoisonError};
use std::thread;
use std::time::Duration;

use crate::{utf8_consumable_prefix_len, CommandSessionOutput};

use super::{interrupt_process_group, open_pty_pair, terminate_process_group};

pub struct CommandSessionProcess {
    pgid: Option<i32>,
    writer: Mutex<File>,
    reader_done: Mutex<Option<mpsc::Receiver<()>>>,
    child: Mutex<Option<Child>>,
}

pub enum ProcessReap {
    Running,
    Exited(Option<ExitStatus>),
}

impl CommandSessionProcess {
    #[must_use]
    pub fn inactive(writer: File) -> Self {
        Self {
            pgid: None,
            writer: Mutex::new(writer),
            reader_done: Mutex::new(None),
            child: Mutex::new(None),
        }
    }

    pub fn write_stdin(&self, bytes: &[u8]) -> io::Result<()> {
        lock(&self.writer).write_all(bytes)
    }

    pub fn interrupt(&self) {
        if let Some(pgid) = self.pgid {
            interrupt_process_group(pgid);
        }
    }

    pub fn terminate(&self) {
        if let Some(pgid) = self.pgid {
            terminate_process_group(pgid);
        }
    }

    #[must_use]
    pub fn try_reap(&self) -> ProcessReap {
        let mut child = lock(&self.child);
        match child.as_mut() {
            Some(handle) => match handle.try_wait() {
                Ok(Some(status)) => {
                    let _ = child.take();
                    ProcessReap::Exited(Some(status))
                }
                Ok(None) => ProcessReap::Running,
                Err(_) => {
                    let _ = child.take();
                    ProcessReap::Exited(None)
                }
            },
            None => ProcessReap::Exited(None),
        }
    }

    pub fn wait_for_reader_done(&self, timeout: Duration) {
        let reader_done = lock(&self.reader_done).take();
        if let Some(reader_done) = reader_done {
            let _ = reader_done.recv_timeout(timeout);
        }
    }
}

pub fn spawn_current_exe_ns_runner(
    request_path: &Path,
    output_path: &Path,
    transcript_path: PathBuf,
    output: std::sync::Arc<CommandSessionOutput>,
) -> io::Result<CommandSessionProcess> {
    let (master, slave) = open_pty_pair()?;
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
    let reader_done = spawn_command_output_reader(master, output, transcript_path);

    Ok(CommandSessionProcess {
        pgid: Some(pgid),
        writer: Mutex::new(writer),
        reader_done: Mutex::new(Some(reader_done)),
        child: Mutex::new(Some(child)),
    })
}

fn spawn_command_output_reader(
    mut master: File,
    output: std::sync::Arc<CommandSessionOutput>,
    transcript_path: PathBuf,
) -> mpsc::Receiver<()> {
    let (done_tx, done_rx) = mpsc::channel();
    thread::spawn(move || {
        let mut transcript = OpenOptions::new()
            .create(true)
            .append(true)
            .open(transcript_path)
            .ok();
        let mut buf = [0_u8; 8192];
        let mut carry: Vec<u8> = Vec::new();
        loop {
            match master.read(&mut buf) {
                Ok(0) => break,
                Ok(n) => {
                    if output.note_spooled(u64::try_from(n).unwrap_or(u64::MAX)) {
                        if let Some(file) = transcript.as_mut() {
                            let _ = file.write_all(&buf[..n]);
                        }
                    }
                    carry.extend_from_slice(&buf[..n]);
                    let consume = utf8_consumable_prefix_len(&carry);
                    if consume > 0 {
                        output.append(String::from_utf8_lossy(&carry[..consume]).into_owned());
                        carry.drain(..consume);
                    }
                }
                Err(err) if err.kind() == io::ErrorKind::Interrupted => {}
                Err(_) => break,
            }
        }
        if !carry.is_empty() {
            output.append(String::from_utf8_lossy(&carry).into_owned());
        }
        let _ = done_tx.send(());
    });
    done_rx
}

fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(PoisonError::into_inner)
}
