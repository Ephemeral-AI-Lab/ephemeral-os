use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

use sandbox_runtime_layerstack::{LayerChange, LayerPath};
use sandbox_runtime_workspace::overlay::capture::capture_upperdir;
use sandbox_runtime_workspace::{ProtectedPathDrop, ProtectedPathDropReason};

// Whiteout/opaque fixtures fabricate kernel overlay metadata via user-namespace
// xattrs (settable unprivileged), so delete/opaque capture coverage is
// Linux-gated: the daemon target is Linux, and dirent names no longer stand in
// for markers.
#[cfg(target_os = "linux")]
#[test]
fn captures_upperdir_files_whiteouts_symlinks_and_opaque_markers(
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let fixture = Fixture::new("capture_upperdir")?;
    std::fs::create_dir_all(fixture.base.join("dir"))?;
    std::fs::write(fixture.base.join("dir/file.txt"), b"hello")?;
    std::fs::write(fixture.base.join("old.txt"), b"")?;
    rustix::fs::lsetxattr(
        fixture.base.join("old.txt"),
        "user.overlay.whiteout",
        b"y",
        rustix::fs::XattrFlags::empty(),
    )?;
    rustix::fs::lsetxattr(
        fixture.base.join("dir"),
        "user.overlay.opaque",
        b"y",
        rustix::fs::XattrFlags::empty(),
    )?;
    std::os::unix::fs::symlink("../target", fixture.base.join("link"))?;

    let captured = capture_upperdir(&fixture.base)?;

    assert!(captured.changes.contains(&LayerChange::WriteFile {
        path: LayerPath::parse("dir/file.txt")?,
        source_path: fixture.base.join("dir/file.txt"),
        size: 5,
    }));
    assert!(captured.changes.contains(&LayerChange::Delete {
        path: LayerPath::parse("old.txt")?,
    }));
    assert!(captured.changes.contains(&LayerChange::Symlink {
        path: LayerPath::parse("link")?,
        source_path: "../target".to_owned(),
    }));
    assert!(captured.changes.contains(&LayerChange::OpaqueDir {
        path: LayerPath::parse("dir")?,
    }));
    Ok(())
}

#[test]
fn plain_wh_named_file_is_captured_as_write_not_delete(
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let fixture = Fixture::new("capture_plain_wh_named_file")?;
    std::fs::write(fixture.base.join(".wh.foo"), b"payload")?;
    std::fs::write(fixture.base.join("foo"), b"sibling")?;

    let captured = capture_upperdir(&fixture.base)?;

    assert!(captured.changes.contains(&LayerChange::WriteFile {
        path: LayerPath::parse(".wh.foo")?,
        source_path: fixture.base.join(".wh.foo"),
        size: 7,
    }));
    assert!(captured.changes.contains(&LayerChange::WriteFile {
        path: LayerPath::parse("foo")?,
        source_path: fixture.base.join("foo"),
        size: 7,
    }));
    assert!(
        !captured
            .changes
            .iter()
            .any(|change| matches!(change, LayerChange::Delete { .. })),
        "a dirent named .wh.foo must never fabricate a delete: {:?}",
        captured.changes
    );
    Ok(())
}

#[test]
fn plain_opaque_marker_named_file_is_not_captured_as_opaque_dir(
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let fixture = Fixture::new("capture_plain_opaque_marker_named_file")?;
    std::fs::create_dir_all(fixture.base.join("dir"))?;
    std::fs::write(fixture.base.join("dir/.wh..wh..opq"), b"x")?;

    let captured = capture_upperdir(&fixture.base)?;

    assert!(captured.changes.contains(&LayerChange::WriteFile {
        path: LayerPath::parse("dir/.wh..wh..opq")?,
        source_path: fixture.base.join("dir/.wh..wh..opq"),
        size: 1,
    }));
    assert!(
        !captured
            .changes
            .iter()
            .any(|change| matches!(change, LayerChange::OpaqueDir { .. })),
        "a dirent named .wh..wh..opq must never fabricate an opaque dir: {:?}",
        captured.changes
    );
    Ok(())
}

#[test]
fn bare_wh_file_is_captured_as_write() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let fixture = Fixture::new("capture_bare_wh_file")?;
    std::fs::write(fixture.base.join(".wh."), b"bare")?;

    let captured = capture_upperdir(&fixture.base)?;

    assert_eq!(
        captured.changes,
        vec![LayerChange::WriteFile {
            path: LayerPath::parse(".wh.")?,
            source_path: fixture.base.join(".wh."),
            size: 4,
        }]
    );
    assert!(captured.protected_drops.is_empty());
    Ok(())
}

#[cfg(unix)]
#[test]
fn captures_unsupported_special_files_as_workspace_protected_drops(
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let fixture = Fixture::new("capture_unsupported_special_file")?;
    let fifo_path = fixture.base.join("run.fifo");
    let status = std::process::Command::new("mkfifo")
        .arg(&fifo_path)
        .status()?;
    assert!(status.success(), "mkfifo failed with status {status}");
    std::fs::write(fixture.base.join("file.txt"), b"regular")?;

    let captured = capture_upperdir(&fixture.base)?;

    assert!(captured.changes.contains(&LayerChange::WriteFile {
        path: LayerPath::parse("file.txt")?,
        source_path: fixture.base.join("file.txt"),
        size: 7,
    }));
    assert!(
        captured
            .changes
            .iter()
            .all(|change| change.path().as_str() != "run.fifo"),
        "unsupported FIFO must not become a layer payload"
    );
    assert_eq!(
        captured.protected_drops,
        vec![ProtectedPathDrop {
            path: "run.fifo".to_owned(),
            reason: ProtectedPathDropReason::UnsupportedSpecialFile,
        }]
    );
    Ok(())
}

#[cfg(target_os = "linux")]
#[test]
fn captures_non_utf8_layer_paths_as_invalid_layer_path_drops(
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    use std::ffi::OsString;
    use std::os::unix::ffi::OsStringExt;

    let fixture = Fixture::new("capture_non_utf8_layer_path")?;
    let bad_name = OsString::from_vec(vec![b'b', 0xff, b'd']);
    std::fs::write(fixture.base.join(bad_name), b"invalid")?;
    std::fs::write(fixture.base.join("file.txt"), b"regular")?;

    let captured = capture_upperdir(&fixture.base)?;

    assert_eq!(
        captured.changes,
        vec![LayerChange::WriteFile {
            path: LayerPath::parse("file.txt")?,
            source_path: fixture.base.join("file.txt"),
            size: 7,
        }]
    );
    assert_eq!(captured.protected_drops.len(), 1);
    assert_eq!(
        captured.protected_drops[0].reason,
        ProtectedPathDropReason::InvalidLayerPath
    );
    assert!(
        captured.protected_drops[0]
            .path
            .starts_with(".invalid-layer-path/"),
        "invalid layer path drops use a stable representable placeholder: {:?}",
        captured.protected_drops[0]
    );
    Ok(())
}

struct Fixture {
    base: PathBuf,
}

impl Fixture {
    fn new(label: &str) -> Result<Self, Box<dyn std::error::Error + Send + Sync>> {
        static COUNTER: AtomicU64 = AtomicU64::new(0);
        let base = std::env::temp_dir().join(format!(
            "workspace-{label}-{}-{}",
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
