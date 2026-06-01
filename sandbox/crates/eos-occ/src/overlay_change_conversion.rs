//! The ONE-WAY occ -> overlay edge: convert overlay captures into OCC changes.
//!
//! This is the single reason `eos-occ` links `eos-overlay`. The edge is
//! strictly one-way (overlay never links occ), which keeps the occ/overlay axis
//! acyclic. Nothing else in this crate touches overlay.

use eos_protocol::LayerChange;

use crate::error::OccError;

pub use eos_overlay::OverlayPathChange;

/// Convert policy-blind overlay captures into typed OCC mutations.
///
/// The overlay crate owns the capture field validation and the storage-level
/// `LayerChange` conversion; OCC only wraps conversion failures in its own
/// error algebra so callers get one publish-path error type.
///
/// # Errors
///
/// Returns [`OccError::InvalidOverlayChange`] when any captured overlay change
/// cannot be converted into a storage-level mutation.
// PORT backend/src/sandbox/occ/overlay_change_conversion.py:16 — overlay.path_change -> OCC changes
pub fn overlay_path_changes_to_occ_changes(
    path_changes: &[OverlayPathChange],
) -> Result<Vec<LayerChange>, OccError> {
    path_changes
        .iter()
        .map(|change| {
            let path = change.path.clone();
            change
                .clone()
                .into_layer_change()
                .map_err(|err| OccError::InvalidOverlayChange {
                    path,
                    reason: err.to_string(),
                })
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicU64, Ordering};

    use eos_overlay::OverlayPathChangeKind;
    use eos_protocol::LayerPath;

    use super::*;

    type TestResult<T = ()> = Result<T, Box<dyn std::error::Error + Send + Sync>>;

    #[test]
    fn converts_write_delete_symlink_and_opaque_dir() -> TestResult {
        let fixture = Fixture::new("overlay_convert")?;
        let write_path = fixture.base.join("content.txt");
        std::fs::write(&write_path, b"hello")?;
        let link_path = fixture.base.join("link");
        std::os::unix::fs::symlink("../target", &link_path)?;

        let changes = overlay_path_changes_to_occ_changes(&[
            OverlayPathChange::new(
                "a.txt",
                OverlayPathChangeKind::Write,
                Some(write_path.to_string_lossy().into_owned()),
                Some("hash".to_owned()),
            )?,
            OverlayPathChange::new("old.txt", OverlayPathChangeKind::Delete, None, None)?,
            OverlayPathChange::new(
                "link.txt",
                OverlayPathChangeKind::Symlink,
                Some(link_path.to_string_lossy().into_owned()),
                Some("hash".to_owned()),
            )?,
            OverlayPathChange::new("dir", OverlayPathChangeKind::OpaqueDir, None, None)?,
        ])?;

        assert_eq!(
            changes,
            vec![
                LayerChange::Write {
                    path: LayerPath::parse("a.txt")?,
                    content: b"hello".to_vec(),
                },
                LayerChange::Delete {
                    path: LayerPath::parse("old.txt")?,
                },
                LayerChange::Symlink {
                    path: LayerPath::parse("link.txt")?,
                    source_path: "../target".to_owned(),
                },
                LayerChange::OpaqueDir {
                    path: LayerPath::parse("dir")?,
                },
            ]
        );
        Ok(())
    }

    #[test]
    fn conversion_error_is_wrapped_as_occ_error() -> TestResult {
        let err = match overlay_path_changes_to_occ_changes(&[OverlayPathChange {
            kind: OverlayPathChangeKind::Write,
            path: "a.txt".to_owned(),
            content_path: None,
            final_hash: Some("hash".to_owned()),
        }]) {
            Ok(changes) => {
                return Err(std::io::Error::other(format!(
                    "missing content path unexpectedly converted: {changes:?}"
                ))
                .into());
            }
            Err(error) => error,
        };

        assert!(matches!(err, OccError::InvalidOverlayChange { .. }));
        Ok(())
    }

    struct Fixture {
        base: PathBuf,
    }

    impl Fixture {
        fn new(label: &str) -> TestResult<Self> {
            static COUNTER: AtomicU64 = AtomicU64::new(0);
            let base = std::env::temp_dir().join(format!(
                "eos-occ-{label}-{}-{}",
                std::process::id(),
                COUNTER.fetch_add(1, Ordering::Relaxed)
            ));
            let _ = std::fs::remove_dir_all(&base);
            std::fs::create_dir_all(&base)?;
            Ok(Self { base })
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.base);
        }
    }
}
