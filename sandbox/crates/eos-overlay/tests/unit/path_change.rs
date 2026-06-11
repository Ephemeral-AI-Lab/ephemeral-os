use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

use super::*;

type TestResult<T = ()> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;

#[test]
fn validates_overlay_path_change_fields() {
    assert!(OverlayPathChange::new(
        "a.txt",
        OverlayPathChangeKind::Write,
        Some("/tmp/a".to_owned()),
        Some("hash".to_owned()),
    )
    .is_ok());
    assert!(OverlayPathChange::new("a.txt", OverlayPathChangeKind::Write, None, None).is_err());
    assert!(OverlayPathChange::new(
        "a.txt",
        OverlayPathChangeKind::Delete,
        Some("/tmp/a".to_owned()),
        None,
    )
    .is_err());
}

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

struct Fixture {
    base: PathBuf,
}

impl Fixture {
    fn new(label: &str) -> TestResult<Self> {
        static COUNTER: AtomicU64 = AtomicU64::new(0);
        let base = std::env::temp_dir().join(format!(
            "eos-overlay-{label}-{}-{}",
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
