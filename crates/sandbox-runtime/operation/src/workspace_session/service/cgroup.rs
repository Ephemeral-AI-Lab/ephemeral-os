use std::fs::File;
use std::io::{self, Read};
use std::path::Path;
use std::thread;
use std::time::{Duration, Instant};

const CGROUP_DRAIN_TIMEOUT: Duration = Duration::from_secs(1);
const CGROUP_DRAIN_INTERVAL: Duration = Duration::from_millis(5);
const CGROUP_EVENTS_LIMIT: u64 = 4 * 1024;
const KNOWN_CGROUP_FILES: [&str; 8] = [
    "cgroup.kill",
    "cgroup.events",
    "cgroup.procs",
    "cpu.max",
    "memory.high",
    "memory.max",
    "memory.oom.group",
    "pids.max",
];

/// Terminate the complete workload leaf without enumerating or signalling raw
/// PIDs, wait for the kernel's populated bit to drain, and remove the leaf.
/// Missing leaves are already-clean success. Every failure is returned to the
/// session destroy ledger for bounded retry/reconciliation.
pub(super) fn cleanup_workspace_cgroup(path: &Path) -> Result<(), String> {
    cleanup_workspace_cgroup_with_timeout(path, CGROUP_DRAIN_TIMEOUT)
}

fn cleanup_workspace_cgroup_with_timeout(path: &Path, timeout: Duration) -> Result<(), String> {
    if !path.exists() {
        return Ok(());
    }

    let kill_path = path.join("cgroup.kill");
    let events_path = path.join("cgroup.events");
    let has_kill = kill_path.exists();
    let has_events = events_path.exists();

    if has_kill {
        std::fs::write(&kill_path, "1")
            .map_err(|error| format!("write {}: {error}", kill_path.display()))?;
        if !has_events {
            return Err(format!(
                "{} exists but {} is unavailable; drain cannot be verified",
                kill_path.display(),
                events_path.display()
            ));
        }
    }

    if has_events {
        let deadline = Instant::now() + timeout;
        loop {
            match read_populated(&events_path)? {
                false => break,
                true if !has_kill => {
                    return Err(format!(
                        "{} reports populated=1 but cgroup.kill is unavailable",
                        events_path.display()
                    ));
                }
                true if Instant::now() >= deadline => {
                    return Err(format!(
                        "{} remained populated after {} ms",
                        events_path.display(),
                        timeout.as_millis()
                    ));
                }
                true => thread::sleep(CGROUP_DRAIN_INTERVAL.min(timeout)),
            }
        }
    }

    match std::fs::remove_dir(path) {
        Ok(()) => return Ok(()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(()),
        Err(error) if error.kind() != io::ErrorKind::DirectoryNotEmpty => {
            return Err(format!("remove {}: {error}", path.display()));
        }
        Err(_) => {}
    }

    for name in KNOWN_CGROUP_FILES {
        let control = path.join(name);
        match std::fs::remove_file(&control) {
            Ok(()) => {}
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => return Err(format!("remove {}: {error}", control.display())),
        }
    }
    std::fs::remove_dir(path).map_err(|error| format!("remove {}: {error}", path.display()))
}

fn read_populated(path: &Path) -> Result<bool, String> {
    let mut bytes = Vec::new();
    File::open(path)
        .and_then(|file| file.take(CGROUP_EVENTS_LIMIT + 1).read_to_end(&mut bytes))
        .map_err(|error| format!("read {}: {error}", path.display()))?;
    if bytes.len() as u64 > CGROUP_EVENTS_LIMIT {
        return Err(format!(
            "{} exceeds the {} byte read bound",
            path.display(),
            CGROUP_EVENTS_LIMIT
        ));
    }
    let text =
        std::str::from_utf8(&bytes).map_err(|_| format!("{} is not UTF-8", path.display()))?;
    for line in text.lines() {
        let mut fields = line.split_whitespace();
        if fields.next() == Some("populated") {
            return match fields.next() {
                Some("0") => Ok(false),
                Some("1") => Ok(true),
                _ => Err(format!("{} has invalid populated value", path.display())),
            };
        }
    }
    Err(format!("{} lacks a populated field", path.display()))
}
