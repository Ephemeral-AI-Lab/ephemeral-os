//! Staged-switch remount runner: C3 steps 1–9 inside the holder namespaces.
//!
//! Build the NEW overlay at a staging point under the session run dir (same
//! upperdir, fresh sibling workdir, production builder) inside the unmask
//! window, restore masks BEFORE any move, probe through the pre-opened
//! staging mount-root fd, `MS_MOVE` old→rollback then staging→root, probe,
//! then strictly unmount the masked rollback through its dirfd's
//! `/proc/self/fd/N` magic path. Every outcome is a report of two booleans
//! plus free-form detail; `mount_verified` is true only when build, probe,
//! both moves, and the visible probe all succeeded.

use std::fs;
use std::os::fd::{AsRawFd, OwnedFd};
use std::path::{Path, PathBuf};

use sandbox_runtime_overlay::{
    mount_overlay, move_mountpoint, strict_unmount, OverlayError, OverlayHandle,
};
use serde_json::json;

use crate::runner::protocol::{NamespaceRunnerRequest, RunResult};
use crate::runner::RunnerError;

pub(crate) const DETAIL_SWITCHED: &str = "switched";
pub(crate) const DETAIL_ROLLBACK_UNMOUNT_BUSY: &str = "pinned:rollback_unmount_busy";

pub(crate) fn run_remount_overlay(
    request: &NamespaceRunnerRequest,
    hidden_paths: &[PathBuf],
) -> Result<RunResult, RunnerError> {
    super::namespaces::setns_user_mnt(request, "setns remount overlay")?;
    Ok(staged_switch(request, hidden_paths))
}

pub(crate) fn report(first_move_succeeded: bool, mount_verified: bool, detail: &str) -> RunResult {
    RunResult {
        exit_code: 0,
        payload: json!({
            "first_move_succeeded": first_move_succeeded,
            "mount_verified": mount_verified,
            "detail": detail,
        }),
    }
}

fn staged_switch(request: &NamespaceRunnerRequest, hidden_paths: &[PathBuf]) -> RunResult {
    let Some(upperdir) = request.upperdir.as_ref() else {
        return report(false, false, "stage_failed:missing_upperdir");
    };
    let Some(workdir) = request.workdir.as_ref() else {
        return report(false, false, "stage_failed:missing_fresh_workdir");
    };
    let Some(run_dir) = workdir.parent().map(Path::to_path_buf) else {
        return report(false, false, "stage_failed:workdir_has_no_run_dir");
    };
    if request.layer_paths.is_empty() {
        return report(false, false, "stage_failed:missing_layer_paths");
    }
    let nonce = std::process::id();
    let staging = run_dir.join(format!(".remount-staging-{nonce}"));
    let rollback = run_dir.join(format!(".remount-rollback-{nonce}"));

    let mut masks = match MaskGuard::lift(hidden_paths) {
        Ok(masks) => masks,
        Err(error) => return report(false, false, &format!("stage_failed:mask_lift:{error}")),
    };
    if let Err(error) = fs::create_dir_all(&staging).and_then(|()| fs::create_dir_all(&rollback)) {
        return report(false, false, &format!("stage_failed:scratch_dirs:{error}"));
    }
    let handle = OverlayHandle {
        upperdir: upperdir.clone(),
        workdir: workdir.clone(),
        layer_paths: request.layer_paths.clone(),
    };
    let staged = match mount_overlay(&staging, &handle) {
        Ok(staged) => staged,
        Err(error) => {
            return report(
                false,
                false,
                &format!("stage_failed:staging_mount:{}", errno_of(&error)),
            )
        }
    };
    std::mem::forget(staged);

    let staging_fd = match open_opath(&staging) {
        Ok(fd) => fd,
        Err(error) => {
            let _ = strict_unmount(&staging);
            return report(false, false, &format!("stage_failed:staging_fd:{error}"));
        }
    };
    let rollback_fd = match open_opath(&rollback) {
        Ok(fd) => fd,
        Err(error) => {
            let _ = strict_unmount(&staging);
            return report(false, false, &format!("stage_failed:rollback_fd:{error}"));
        }
    };
    let ws_old_fd = match open_opath(&request.workspace_root) {
        Ok(fd) => fd,
        Err(error) => {
            let _ = strict_unmount(&staging);
            return report(false, false, &format!("stage_failed:workspace_fd:{error}"));
        }
    };

    if let Err(error) = masks.restore(hidden_paths) {
        let _ = strict_unmount(&magic_path(&staging_fd));
        return report(
            false,
            false,
            &format!("stage_failed:mask_restore_failed:{error}"),
        );
    }
    if let Err(detail) = probe_overlay_root(&staging_fd) {
        let _ = strict_unmount(&magic_path(&staging_fd));
        return report(
            false,
            false,
            &format!("stage_failed:staged_probe_mismatch:{detail}"),
        );
    }

    if let Err(error) = move_mountpoint(&ws_old_fd, &rollback_fd) {
        let _ = strict_unmount(&magic_path(&staging_fd));
        return report(
            false,
            false,
            &format!("move_failed:first_move:{}", errno_of(&error)),
        );
    }

    let ws_under_fd = match open_opath(&request.workspace_root) {
        Ok(fd) => fd,
        Err(error) => {
            return report(
                true,
                false,
                &format!("mount_uncertain:workspace_reopen:{error}"),
            )
        }
    };
    if let Err(error) = move_mountpoint(&staging_fd, &ws_under_fd) {
        return report(
            true,
            false,
            &format!("mount_uncertain:second_move:{}", errno_of(&error)),
        );
    }
    if let Err(detail) = probe_overlay_root(&staging_fd) {
        return report(
            true,
            false,
            &format!("mount_uncertain:visible_probe:{detail}"),
        );
    }

    drop(ws_old_fd);
    match strict_unmount(&magic_path(&rollback_fd)) {
        Ok(()) => report(true, true, DETAIL_SWITCHED),
        Err(error) if errno_of(&error) == libc::EBUSY => {
            report(true, true, DETAIL_ROLLBACK_UNMOUNT_BUSY)
        }
        Err(error) => report(
            true,
            true,
            &format!("rollback_unmount_failed:{}", errno_of(&error)),
        ),
    }
}

struct MaskGuard {
    hidden: Vec<PathBuf>,
    restored: bool,
}

impl MaskGuard {
    fn lift(hidden_paths: &[PathBuf]) -> Result<Self, String> {
        for path in hidden_paths {
            match strict_unmount(path) {
                Ok(()) => {}
                Err(error) if matches!(errno_of(&error), libc::EINVAL | libc::ENOENT) => {}
                Err(error) => return Err(format!("{}:{}", path.display(), errno_of(&error))),
            }
        }
        Ok(Self {
            hidden: hidden_paths.to_vec(),
            restored: false,
        })
    }

    fn restore(&mut self, hidden_paths: &[PathBuf]) -> Result<(), RunnerError> {
        crate::runner::mask_model_shell_paths(hidden_paths)?;
        self.restored = true;
        Ok(())
    }
}

/// Every early return before the explicit restore re-masks best-effort, so
/// no abort path can hand resumed tasks an unmasked namespace.
impl Drop for MaskGuard {
    fn drop(&mut self) {
        if !self.restored {
            let _ = crate::runner::mask_model_shell_paths(&self.hidden);
        }
    }
}

fn probe_overlay_root(mount_root_fd: &OwnedFd) -> Result<(), String> {
    const OVERLAYFS_SUPER_MAGIC: i64 = 0x794c_7630;
    // SAFETY: `libc::statfs` is a plain-old-data struct for which all-zeroes
    // is a valid bit pattern.
    let mut stat: libc::statfs = unsafe { std::mem::zeroed() };
    // SAFETY: `fstatfs` writes into the zeroed statfs buffer for a valid
    // borrowed fd and reads nothing else.
    let rc = unsafe { libc::fstatfs(mount_root_fd.as_raw_fd(), &mut stat) };
    if rc != 0 {
        return Err(format!("fstatfs:{}", std::io::Error::last_os_error()));
    }
    let fs_type = stat.f_type as i64;
    if fs_type != OVERLAYFS_SUPER_MAGIC {
        return Err(format!("fstype:0x{fs_type:x}"));
    }
    fs::read_dir(magic_path(mount_root_fd))
        .map(drop)
        .map_err(|error| format!("readdir:{error}"))
}

fn magic_path(fd: &OwnedFd) -> PathBuf {
    PathBuf::from(format!("/proc/self/fd/{}", fd.as_raw_fd()))
}

fn open_opath(path: &Path) -> Result<OwnedFd, std::io::Error> {
    rustix::fs::open(
        path,
        rustix::fs::OFlags::PATH | rustix::fs::OFlags::CLOEXEC,
        rustix::fs::Mode::empty(),
    )
    .map_err(std::io::Error::from)
}

fn errno_of(error: &OverlayError) -> i32 {
    match error {
        OverlayError::MountSyscall { source, .. } => source.raw_os_error().unwrap_or(-1),
        _ => -1,
    }
}
