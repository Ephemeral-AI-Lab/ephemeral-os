use std::path::{Path, PathBuf};

use crate::capture::{capture_upperdir, CapturedChanges};
use crate::dirs::{DirAllocator, OverlayDirs};
use crate::EphemeralWorkspaceError;

/// One overlay transaction: scratch dirs bound to a frozen layer-path set.
///
/// Dropping the workspace removes its run directory (best-effort), so the
/// settle paths are simply: `capture()` then drop on success, plain drop on
/// cancel/discard. The lease that froze `layer_paths` stays with whoever
/// acquired it.
#[derive(Debug)]
pub struct EphemeralWorkspace {
    workspace_root: PathBuf,
    layer_paths: Vec<PathBuf>,
    dirs: OverlayDirs,
    keep_on_drop: bool,
}

/// Everything a runner child needs to mount the overlay.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct MountPlan<'a> {
    /// Mount target and exec cwd inside the namespace.
    pub workspace_root: &'a Path,
    /// Frozen lower layers, newest-first.
    pub layer_paths: &'a [PathBuf],
    pub upperdir: &'a Path,
    pub workdir: &'a Path,
}

impl EphemeralWorkspace {
    /// Allocate fresh overlay dirs under `scratch_root` for one operation.
    ///
    /// `kind` and `token` only shape the scratch directory name (sanitized);
    /// `layer_paths` is the snapshot's frozen lower-layer list, newest-first.
    ///
    /// # Errors
    ///
    /// Returns [`EphemeralWorkspaceError::DirAllocation`] when scratch
    /// directories cannot be created.
    pub fn create(
        scratch_root: &Path,
        kind: &str,
        token: &str,
        workspace_root: PathBuf,
        layer_paths: Vec<PathBuf>,
    ) -> Result<Self, EphemeralWorkspaceError> {
        let dirs = DirAllocator::new(scratch_root.to_path_buf()).allocate(kind, token)?;
        Ok(Self {
            workspace_root,
            layer_paths,
            dirs,
            keep_on_drop: false,
        })
    }

    #[must_use]
    pub fn mount_plan(&self) -> MountPlan<'_> {
        MountPlan {
            workspace_root: &self.workspace_root,
            layer_paths: &self.layer_paths,
            upperdir: &self.dirs.upperdir,
            workdir: &self.dirs.workdir,
        }
    }

    #[must_use]
    pub fn dirs(&self) -> &OverlayDirs {
        &self.dirs
    }

    /// Capture the upperdir delta for publishing.
    ///
    /// Non-consuming: the caller publishes the returned changes and then drops
    /// the workspace, so a failed publish can still inspect the dirs.
    ///
    /// # Errors
    ///
    /// Returns [`EphemeralWorkspaceError::CaptureFailed`] when the overlay
    /// capture walk fails.
    pub fn capture(&self) -> Result<CapturedChanges, EphemeralWorkspaceError> {
        capture_upperdir(&self.dirs.upperdir)
    }

    /// Leak the scratch dirs instead of removing them on drop (diagnostics).
    pub fn keep_on_drop(&mut self) {
        self.keep_on_drop = true;
    }
}

impl Drop for EphemeralWorkspace {
    fn drop(&mut self) {
        if !self.keep_on_drop {
            let _ = std::fs::remove_dir_all(&self.dirs.run_dir);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    type TestResult<T = ()> = Result<T, Box<dyn std::error::Error + Send + Sync>>;

    fn scratch(label: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "eos-ephemeral-ws-{label}-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        dir
    }

    #[test]
    fn create_capture_and_drop_cleans_scratch() -> TestResult {
        let scratch = scratch("lifecycle");
        let ws = EphemeralWorkspace::create(
            &scratch,
            "command",
            "inv-1",
            PathBuf::from("/workspace"),
            vec![PathBuf::from("/stack/layers/a")],
        )?;
        let run_dir = ws.dirs().run_dir.clone();
        std::fs::create_dir_all(ws.dirs().upperdir.join("nested"))?;
        std::fs::write(ws.dirs().upperdir.join("nested/new.txt"), b"hello")?;

        let plan = ws.mount_plan();
        assert_eq!(plan.workspace_root, Path::new("/workspace"));
        assert_eq!(plan.layer_paths, &[PathBuf::from("/stack/layers/a")]);

        let captured = ws.capture()?;
        assert_eq!(captured.changes.len(), 1, "one written path captured");
        assert_eq!(captured.path_kinds[0].path, "nested/new.txt");
        assert_eq!(
            captured.path_kinds[0].kind,
            crate::PathChangeKind::Write,
            "regular file classifies as write"
        );
        assert!(captured.stats.bytes >= 5, "stats count written bytes");

        drop(ws);
        assert!(!run_dir.exists(), "drop removes the run dir");
        let _ = std::fs::remove_dir_all(scratch);
        Ok(())
    }

    #[test]
    fn allocator_sanitizes_unsafe_segments() -> TestResult {
        let scratch = scratch("sanitize");
        let dirs = DirAllocator::new(scratch.clone()).allocate("a/b", "../evil")?;
        assert!(dirs.run_dir.starts_with(scratch.join("a_b")), "kind sanitized");
        let leaf = dirs
            .run_dir
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or_default();
        assert!(
            leaf.ends_with("-.._evil"),
            "token slash flattened into one safe segment: {leaf}"
        );
        let _ = std::fs::remove_dir_all(scratch);
        Ok(())
    }
}
