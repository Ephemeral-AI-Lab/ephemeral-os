//! Boot-time kernel gate for live remount (G1 + G2 in one probe).
//!
//! Live remount stands on one load-bearing kernel assumption: OLD and NEW
//! overlays coexist on the same upperdir (NEW with a fresh sibling workdir)
//! through the staged `MS_MOVE` switch, with `userxattr` whiteouts honored so
//! deleted files stay deleted. This runs exactly that sequence through the
//! **production** overlay builder in a scratch userns+mntns; a clean pass
//! means the environment supports live remount. On any failure the daemon
//! keeps squash commit-only and every session reports
//! `leased(unsupported:kernel_gate_not_proven)`.
//!
//! `unshare(CLONE_NEWUSER)` requires a single-threaded caller, so the daemon
//! (multithreaded) runs this in a fresh `gate-probe` **subprocess** —
//! [`probe_live_remount_gate`] spawns it, [`run_gate_probe`] is the
//! subprocess body — mirroring the ns-holder/ns-runner launcher pattern.

use std::path::Path;
#[cfg(target_os = "linux")]
use std::path::PathBuf;

/// Spawn the gate-probe subprocess (`<current_exe> gate-probe <scratch>`) and
/// return whether it exited cleanly. Any spawn/exec/probe failure => not
/// proven => live remount stays disabled.
#[must_use]
pub fn probe_live_remount_gate(scratch_root: &Path) -> bool {
    let Ok(exe) = std::env::current_exe() else {
        return false;
    };
    std::process::Command::new(exe)
        .arg("gate-probe")
        .arg(scratch_root)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .map(|status| status.success())
        .unwrap_or(false)
}

/// The gate-probe subprocess body: run the same-upperdir staged switch +
/// userxattr parity in a scratch namespace under `scratch_root`. Returns
/// `true` on a clean pass. The caller (a fresh single-threaded subprocess)
/// then exits `0`/`1` from this verdict.
#[cfg(target_os = "linux")]
#[must_use]
pub fn run_gate_probe(scratch_root: &Path) -> bool {
    let probe_dir = scratch_root.join(format!(".remount-gate-probe-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&probe_dir);
    if std::fs::create_dir_all(&probe_dir).is_err() {
        return false;
    }
    let verdict = if enter_scratch_namespace() {
        staged_switch_witness(&probe_dir).unwrap_or(false)
    } else {
        false
    };
    let _ = std::fs::remove_dir_all(&probe_dir);
    verdict
}

/// Non-Linux hosts (dev only) cannot mount overlays; the gate never proves.
#[cfg(not(target_os = "linux"))]
#[must_use]
pub fn run_gate_probe(_scratch_root: &Path) -> bool {
    false
}

#[cfg(target_os = "linux")]
fn enter_scratch_namespace() -> bool {
    use rustix::mount::{mount_change, MountPropagationFlags};
    use rustix::thread::UnshareFlags;

    let uid = rustix::process::getuid().as_raw();
    let gid = rustix::process::getgid().as_raw();
    if rustix::thread::unshare(UnshareFlags::NEWUSER | UnshareFlags::NEWNS).is_err() {
        return false;
    }
    let _ = std::fs::write("/proc/self/setgroups", b"deny\n");
    if std::fs::write("/proc/self/uid_map", format!("0 {uid} 1\n")).is_err()
        || std::fs::write("/proc/self/gid_map", format!("0 {gid} 1\n")).is_err()
    {
        return false;
    }
    rustix::thread::set_thread_gid(rustix::process::Gid::ROOT).is_ok()
        && rustix::thread::set_thread_uid(rustix::process::Uid::ROOT).is_ok()
        && mount_change(
            "/",
            MountPropagationFlags::PRIVATE | MountPropagationFlags::REC,
        )
        .is_ok()
}

#[cfg(target_os = "linux")]
fn staged_switch_witness(dir: &Path) -> Option<bool> {
    use std::io::Write as _;

    use sandbox_runtime_overlay::{mount_overlay, move_mountpoint, strict_unmount, OverlayHandle};

    let lower = dir.join("l1");
    write_file(&lower.join("keep"), "keep")?;
    write_file(&lower.join("doomed"), "doomed")?;

    let upper = dir.join("upper");
    let ws = dir.join("ws");
    let staging = dir.join("staging");
    let rollback = dir.join("rollback");
    // The production builder requires the mountpoint to exist (it creates
    // only upper/work), so pre-create the overlay targets.
    std::fs::create_dir_all(&ws).ok()?;
    std::fs::create_dir_all(&staging).ok()?;
    std::fs::create_dir_all(&rollback).ok()?;

    let old = OverlayHandle {
        upperdir: upper.clone(),
        workdir: dir.join("work-old"),
        layer_paths: vec![lower.clone()],
    };
    std::mem::forget(mount_overlay(&ws, &old).ok()?);

    std::fs::remove_file(ws.join("doomed")).ok()?;
    {
        let mut cow = std::fs::File::create(ws.join("cow")).ok()?;
        cow.write_all(b"NEW").ok()?;
    }

    let new = OverlayHandle {
        upperdir: upper,
        workdir: dir.join("work-remount"),
        layer_paths: vec![lower],
    };
    std::mem::forget(mount_overlay(&staging, &new).ok()?);

    if !witness_holds(&staging) {
        let _ = strict_unmount(&staging);
        return Some(false);
    }

    let staging_fd = open_opath(&staging)?;
    let rollback_fd = open_opath(&rollback)?;
    let ws_old_fd = open_opath(&ws)?;
    move_mountpoint(&ws_old_fd, &rollback_fd).ok()?;
    let ws_under_fd = open_opath(&ws)?;
    move_mountpoint(&staging_fd, &ws_under_fd).ok()?;

    let visible = witness_holds(&ws);
    drop(ws_old_fd);
    let rollback_unmounted = strict_unmount(&magic(&rollback_fd)).is_ok();
    drop(staging_fd);
    Some(visible && rollback_unmounted)
}

#[cfg(target_os = "linux")]
fn witness_holds(root: &Path) -> bool {
    std::fs::read_to_string(root.join("keep")).ok().as_deref() == Some("keep")
        && !root.join("doomed").exists()
        && std::fs::read_to_string(root.join("cow")).ok().as_deref() == Some("NEW")
}

#[cfg(target_os = "linux")]
fn write_file(path: &Path, content: &str) -> Option<()> {
    std::fs::create_dir_all(path.parent()?).ok()?;
    std::fs::write(path, content).ok()
}

#[cfg(target_os = "linux")]
fn open_opath(path: &Path) -> Option<std::os::fd::OwnedFd> {
    rustix::fs::open(
        path,
        rustix::fs::OFlags::PATH | rustix::fs::OFlags::CLOEXEC,
        rustix::fs::Mode::empty(),
    )
    .ok()
}

#[cfg(target_os = "linux")]
fn magic(fd: &std::os::fd::OwnedFd) -> PathBuf {
    use std::os::fd::AsRawFd;
    PathBuf::from(format!("/proc/self/fd/{}", fd.as_raw_fd()))
}
