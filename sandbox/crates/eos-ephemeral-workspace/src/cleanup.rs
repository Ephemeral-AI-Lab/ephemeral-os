use std::path::Path;
use std::time::Instant;

use serde::{Deserialize, Serialize};

use crate::ports::EphemeralSnapshotPort;
use crate::types::{EphemeralSnapshot, WorkspaceRoot};

/// Best-effort cleanup result for a fresh ephemeral operation.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CleanupOutcome {
    pub released_lease: bool,
    pub removed_run_dir: bool,
    pub cleanup_s: f64,
    pub errors: Vec<String>,
}

/// Release the snapshot lease and remove the fresh run directory.
#[must_use]
pub fn cleanup_ephemeral_workspace<S>(
    snapshots: &S,
    root: &WorkspaceRoot,
    snapshot: &EphemeralSnapshot,
    run_dir: &Path,
) -> CleanupOutcome
where
    S: EphemeralSnapshotPort,
{
    let start = Instant::now();
    let mut errors = Vec::new();
    let released_lease = match snapshots.release_lease(root, &snapshot.lease_id) {
        Ok(released) => released,
        Err(error) => {
            errors.push(error.to_string());
            false
        }
    };
    let removed_run_dir = match std::fs::remove_dir_all(run_dir) {
        Ok(()) => true,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => true,
        Err(error) => {
            errors.push(error.to_string());
            false
        }
    };

    CleanupOutcome {
        released_lease,
        removed_run_dir,
        cleanup_s: start.elapsed().as_secs_f64(),
        errors,
    }
}

#[cfg(test)]
mod tests {
    use std::cell::RefCell;
    use std::path::PathBuf;

    use crate::error::EphemeralWorkspaceError;
    use crate::ports::EphemeralSnapshotPort;
    use crate::types::{EphemeralSnapshot, WorkspaceRoot};

    use super::cleanup_ephemeral_workspace;

    #[test]
    fn cleanup_releases_lease_and_removes_run_dir() -> Result<(), Box<dyn std::error::Error>> {
        let run_dir = unique_temp_dir("cleanup-ok");
        std::fs::create_dir_all(&run_dir)?;
        std::fs::write(run_dir.join("result.json"), b"{}")?;
        let snapshots = RecordingSnapshots::default();
        let snapshot = snapshot();

        let outcome = cleanup_ephemeral_workspace(
            &snapshots,
            &WorkspaceRoot(PathBuf::from("/stack")),
            &snapshot,
            &run_dir,
        );

        assert!(outcome.released_lease);
        assert!(outcome.removed_run_dir);
        assert!(outcome.errors.is_empty());
        assert!(!run_dir.exists());
        assert_eq!(
            snapshots.released.borrow().as_slice(),
            [snapshot.lease_id.clone()]
        );
        Ok(())
    }

    #[test]
    fn cleanup_reports_release_error_but_still_removes_run_dir(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let run_dir = unique_temp_dir("cleanup-release-error");
        std::fs::create_dir_all(&run_dir)?;
        let snapshots = RecordingSnapshots {
            fail_release: true,
            ..RecordingSnapshots::default()
        };

        let outcome = cleanup_ephemeral_workspace(
            &snapshots,
            &WorkspaceRoot(PathBuf::from("/stack")),
            &snapshot(),
            &run_dir,
        );

        assert!(!outcome.released_lease);
        assert!(outcome.removed_run_dir);
        assert_eq!(outcome.errors.len(), 1);
        assert!(!run_dir.exists());
        Ok(())
    }

    #[derive(Default)]
    struct RecordingSnapshots {
        released: RefCell<Vec<String>>,
        fail_release: bool,
    }

    impl EphemeralSnapshotPort for RecordingSnapshots {
        fn acquire_snapshot(
            &self,
            _root: &WorkspaceRoot,
            _request_id: &str,
        ) -> Result<EphemeralSnapshot, EphemeralWorkspaceError> {
            Ok(snapshot())
        }

        fn release_lease(
            &self,
            _root: &WorkspaceRoot,
            lease_id: &str,
        ) -> Result<bool, EphemeralWorkspaceError> {
            self.released.borrow_mut().push(lease_id.to_owned());
            if self.fail_release {
                return Err(EphemeralWorkspaceError::LeaseRelease {
                    lease_id: lease_id.to_owned(),
                    reason: "injected".to_owned(),
                });
            }
            Ok(true)
        }
    }

    fn snapshot() -> EphemeralSnapshot {
        EphemeralSnapshot {
            lease_id: "lease-1".to_owned(),
            manifest_version: 1,
            manifest_root_hash: "root".to_owned(),
            layer_paths: Vec::new(),
        }
    }

    fn unique_temp_dir(prefix: &str) -> PathBuf {
        std::env::temp_dir().join(format!(
            "eos-ephemeral-{prefix}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos()
        ))
    }
}
