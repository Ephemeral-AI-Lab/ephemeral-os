use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

use super::*;

type TestResult<T = ()> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;

#[test]
fn captures_upperdir_files_whiteouts_symlinks_and_opaque_markers() -> TestResult {
    let fixture = Fixture::new("capture_upperdir")?;
    std::fs::create_dir_all(fixture.base.join("dir"))?;
    std::fs::write(fixture.base.join("dir/file.txt"), b"hello")?;
    std::fs::write(fixture.base.join(".wh.old.txt"), b"")?;
    std::fs::write(fixture.base.join("dir").join(OPAQUE_MARKER), b"")?;
    std::os::unix::fs::symlink("../target", fixture.base.join("link"))?;

    let changes = capture_upperdir(&fixture.base)?;

    assert!(changes.contains(&LayerChange::Write {
        path: LayerPath::parse("dir/file.txt")?,
        content: b"hello".to_vec(),
    }));
    assert!(changes.contains(&LayerChange::Delete {
        path: LayerPath::parse("old.txt")?,
    }));
    assert!(changes.contains(&LayerChange::Symlink {
        path: LayerPath::parse("link")?,
        source_path: "../target".to_owned(),
    }));
    assert!(changes.contains(&LayerChange::OpaqueDir {
        path: LayerPath::parse("dir")?,
    }));
    Ok(())
}

#[cfg(unix)]
#[test]
fn captures_unsupported_special_files_as_protected_drops() -> TestResult {
    let fixture = Fixture::new("capture_unsupported_special_file")?;
    let fifo_path = fixture.base.join("run.fifo");
    let status = std::process::Command::new("mkfifo")
        .arg(&fifo_path)
        .status()?;
    assert!(status.success(), "mkfifo failed with status {status}");
    std::fs::write(fixture.base.join("file.txt"), b"regular")?;

    let captured = capture_upperdir_with_stats(&fixture.base)?;

    assert!(captured.changes.contains(&LayerChange::Write {
        path: LayerPath::parse("file.txt")?,
        content: b"regular".to_vec(),
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
            path: LayerPath::parse("run.fifo")?,
            reason: ProtectedPathDropReason::UnsupportedSpecialFile,
        }]
    );
    Ok(())
}

#[test]
fn regular_file_capture_rejects_symlink_replacement_after_classification() -> TestResult {
    let fixture = Fixture::new("capture_symlink_swap")?;
    let entry = fixture.base.join("file.txt");
    let target = fixture.base.join("target.txt");
    std::fs::write(&entry, b"original")?;
    std::fs::write(&target, b"leaked")?;

    let meta = std::fs::symlink_metadata(&entry)?;
    std::fs::remove_file(&entry)?;
    std::os::unix::fs::symlink(&target, &entry)?;

    let mut changes = Vec::new();
    let mut protected_drops = Vec::new();
    let error = capture_file_entry(
        &fixture.base,
        &entry,
        &meta,
        &mut std::collections::HashSet::new(),
        &mut changes,
        &mut protected_drops,
    )
    .expect_err("swapped symlink must not be captured as regular file content");

    assert!(matches!(error, CaptureError::Capture { .. }));
    assert!(changes.is_empty());
    assert!(protected_drops.is_empty());
    Ok(())
}

#[test]
fn regular_file_capture_rejects_oversized_files_before_write_change() -> TestResult {
    let fixture = Fixture::new("capture_oversized_file")?;
    let entry = fixture.base.join("large.txt");
    std::fs::write(&entry, b"abcdef")?;
    let meta = std::fs::symlink_metadata(&entry)?;

    let error = write_change_limited("large.txt", &entry, &meta, 2)
        .expect_err("oversized file capture must be rejected");

    assert!(matches!(error, CaptureError::Capture { .. }));
    assert!(
        error
            .to_string()
            .contains("overlay regular file too large: 6 > 2 bytes"),
        "{error}"
    );
    Ok(())
}

#[cfg(unix)]
#[test]
fn relative_path_conversion_rejects_non_utf8_component() -> TestResult {
    use std::ffi::OsString;
    use std::os::unix::ffi::OsStringExt;

    let bad_name = OsString::from_vec(vec![b'b', 0xff, b'd']);

    let error = relative_to_string(&PathBuf::from(bad_name))
        .expect_err("non-UTF-8 layer paths are rejected");

    assert!(matches!(error, CaptureError::InvalidPathChange(_)));
    assert!(error.to_string().contains("not valid UTF-8"), "{error}");
    Ok(())
}

#[cfg(unix)]
#[test]
fn capture_rejects_non_utf8_symlink_target() -> TestResult {
    use std::ffi::OsString;
    use std::os::unix::ffi::OsStringExt;

    let fixture = Fixture::new("capture_non_utf8_symlink")?;
    let bad_target = PathBuf::from(OsString::from_vec(vec![b't', 0xff, b'g']));
    std::os::unix::fs::symlink(bad_target, fixture.base.join("link"))?;

    let error =
        capture_upperdir(&fixture.base).expect_err("non-UTF-8 symlink targets are rejected");

    assert!(matches!(error, CaptureError::InvalidPathChange(_)));
    assert!(error.to_string().contains("not valid UTF-8"), "{error}");
    Ok(())
}

struct Fixture {
    base: PathBuf,
}

impl Fixture {
    fn new(label: &str) -> TestResult<Self> {
        static COUNTER: AtomicU64 = AtomicU64::new(0);
        let base = std::env::temp_dir().join(format!(
            "layerstack-{label}-{}-{}",
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
