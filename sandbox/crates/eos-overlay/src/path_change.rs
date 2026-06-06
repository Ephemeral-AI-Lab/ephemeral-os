//! Policy-blind path changes captured from a snapshot overlay, plus the
//! ONE-WAY conversion into `eos_protocol::LayerChange`.
//!
//! This conversion lives HERE (occ depends on it one-way; overlay has NO occ
//! dep — the `occ → overlay` edge stays acyclic). The capture half walks ONLY
//! the overlay `upperdir`: capture + publish is one atomic unit per op, so a
//! consumer never observes a partial write set. Other agents never see a
//! half-captured upperdir.

use std::collections::HashSet;
use std::path::{Path, PathBuf};

use eos_protocol::{LayerChange, LayerPath};
use sha2::{Digest, Sha256};

use crate::error::{OverlayError, Result};

const WHITEOUT_PREFIX: &str = ".wh.";
const OPAQUE_MARKER: &str = ".wh..wh..opq";

/// The kind of a captured overlay path change.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OverlayPathChangeKind {
    /// File content write; `content_path` + `final_hash` required.
    Write,
    /// File/dir removal (overlay whiteout).
    Delete,
    /// Symlink; `content_path` (link target capture) + `final_hash` required.
    Symlink,
    /// Opaque-directory marker (root path allowed).
    OpaqueDir,
}

/// A single change captured from the overlay upperdir.
///
/// Before layer-stack policy is applied. `path` is normalized; `write`/`symlink`
/// carry a staged `content_path` + `final_hash`, the others carry neither.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OverlayPathChange {
    /// Normalized relative layer path (root `""` allowed only for `opaque_dir`).
    pub path: String,
    /// The change kind.
    pub kind: OverlayPathChangeKind,
    /// Staged content path on disk (`write`/`symlink` only).
    pub content_path: Option<String>,
    /// `sha256` hex of the staged content (`write`/`symlink` only).
    pub final_hash: Option<String>,
}

impl OverlayPathChange {
    /// Validate-and-construct: normalize the path (root allowed only for
    /// `opaque_dir`), require
    /// `content_path`+`final_hash` for `write`/`symlink`, forbid them otherwise.
    ///
    /// # Errors
    ///
    /// Returns [`OverlayError::InvalidPathChange`] when path normalization or
    /// per-kind payload validation fails.
    pub fn new(
        path: &str,
        kind: OverlayPathChangeKind,
        content_path: Option<String>,
        final_hash: Option<String>,
    ) -> Result<Self> {
        let path = normalize_overlay_path(path, kind == OverlayPathChangeKind::OpaqueDir)?;
        match kind {
            OverlayPathChangeKind::Write | OverlayPathChangeKind::Symlink => {
                if content_path.as_deref().unwrap_or_default().is_empty() {
                    return Err(OverlayError::InvalidPathChange(format!(
                        "{kind:?} changes require content_path"
                    )));
                }
                if final_hash.as_deref().unwrap_or_default().is_empty() {
                    return Err(OverlayError::InvalidPathChange(format!(
                        "{kind:?} changes require final_hash"
                    )));
                }
            }
            OverlayPathChangeKind::Delete | OverlayPathChangeKind::OpaqueDir => {
                if content_path.is_some() {
                    return Err(OverlayError::InvalidPathChange(format!(
                        "{kind:?} changes must not carry content_path"
                    )));
                }
                if final_hash.is_some() {
                    return Err(OverlayError::InvalidPathChange(format!(
                        "{kind:?} changes must not carry final_hash"
                    )));
                }
            }
        }
        Ok(Self {
            path,
            kind,
            content_path,
            final_hash,
        })
    }

    /// Convert this overlay-side change into the storage-level
    /// `eos_protocol::LayerChange`. ONE-WAY: occ consumes this; overlay never
    /// imports occ. `write` threads the precomputed `content_path`/`final_hash`;
    /// `symlink` reads the link target (`os.readlink`).
    ///
    /// # Errors
    ///
    /// Returns [`OverlayError`] when the captured path is invalid or when staged
    /// content/link-target reads fail.
    pub fn into_layer_change(self) -> Result<LayerChange> {
        let path = LayerPath::parse(&self.path)?;
        match self.kind {
            OverlayPathChangeKind::Write => {
                let content_path = self.content_path.ok_or_else(|| {
                    OverlayError::InvalidPathChange("write changes require content_path".to_owned())
                })?;
                let content = std::fs::read(content_path).map_err(OverlayError::Capture)?;
                Ok(LayerChange::Write { path, content })
            }
            OverlayPathChangeKind::Delete => Ok(LayerChange::Delete { path }),
            OverlayPathChangeKind::Symlink => {
                let content_path = self.content_path.ok_or_else(|| {
                    OverlayError::InvalidPathChange(
                        "symlink changes require content_path".to_owned(),
                    )
                })?;
                let source_path = std::fs::read_link(content_path)
                    .map_err(OverlayError::Capture)?
                    .to_string_lossy()
                    .into_owned();
                Ok(LayerChange::Symlink { path, source_path })
            }
            OverlayPathChangeKind::OpaqueDir => Ok(LayerChange::OpaqueDir { path }),
        }
    }
}

/// Walk the overlay `upperdir` and capture the full write set.
///
/// Walks ONLY the upperdir (never the lower layers): capture + publish is one
/// atomic unit, so the returned set is the complete delta for this op. Overlay
/// whiteouts -> `Delete`, opaque markers -> `OpaqueDir`, symlinks -> `Symlink`,
/// regular files -> `Write`.
///
/// # Errors
///
/// Returns [`OverlayError`] when upperdir traversal, path normalization, xattr
/// probing, hashing, or staged content conversion fails.
pub fn capture_upperdir(upperdir: &Path) -> Result<Vec<LayerChange>> {
    std::fs::create_dir_all(upperdir).map_err(OverlayError::Capture)?;
    let mut emitted_opaque_dirs = HashSet::new();
    let mut changes = Vec::new();
    walk_upperdir(upperdir, upperdir, &mut emitted_opaque_dirs, &mut changes)?;
    changes
        .into_iter()
        .map(OverlayPathChange::into_layer_change)
        .collect()
}

fn walk_upperdir(
    root: &Path,
    dir: &Path,
    emitted_opaque_dirs: &mut HashSet<String>,
    changes: &mut Vec<OverlayPathChange>,
) -> Result<()> {
    let mut entries = std::fs::read_dir(dir)
        .map_err(OverlayError::Capture)?
        .collect::<std::result::Result<Vec<_>, _>>()
        .map_err(OverlayError::Capture)?;
    entries.sort_by_key(std::fs::DirEntry::file_name);

    let mut dirs = Vec::new();
    let mut files = Vec::new();
    for entry in entries {
        let file_type = entry.file_type().map_err(OverlayError::Capture)?;
        if file_type.is_dir() {
            dirs.push(entry.path());
        } else {
            files.push(entry.path());
        }
    }

    for entry in files {
        capture_file_entry(root, &entry, emitted_opaque_dirs, changes)?;
    }
    for entry in &dirs {
        if has_overlay_opaque_xattr(entry) {
            let opaque_path = relative_overlay_path(root, entry)?;
            if emitted_opaque_dirs.insert(opaque_path.clone()) {
                changes.push(OverlayPathChange::new(
                    &opaque_path,
                    OverlayPathChangeKind::OpaqueDir,
                    None,
                    None,
                )?);
            }
        }
    }
    for entry in dirs {
        walk_upperdir(root, &entry, emitted_opaque_dirs, changes)?;
    }
    Ok(())
}

fn capture_file_entry(
    root: &Path,
    entry: &Path,
    emitted_opaque_dirs: &mut HashSet<String>,
    changes: &mut Vec<OverlayPathChange>,
) -> Result<()> {
    let rel = relative_path(root, entry)?;
    let name = entry
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or_default();
    if name == OPAQUE_MARKER {
        let opaque_path = rel.parent().map(relative_to_string).unwrap_or_default();
        if emitted_opaque_dirs.insert(opaque_path.clone()) {
            changes.push(OverlayPathChange::new(
                &opaque_path,
                OverlayPathChangeKind::OpaqueDir,
                None,
                None,
            )?);
        }
        return Ok(());
    }
    if is_whiteout_marker(name) {
        let target = whiteout_target(&rel);
        changes.push(OverlayPathChange::new(
            &relative_to_string(&target),
            OverlayPathChangeKind::Delete,
            None,
            None,
        )?);
        return Ok(());
    }
    if is_overlay_whiteout(entry)? {
        changes.push(OverlayPathChange::new(
            &relative_to_string(&rel),
            OverlayPathChangeKind::Delete,
            None,
            None,
        )?);
        return Ok(());
    }
    let meta = std::fs::symlink_metadata(entry).map_err(OverlayError::Capture)?;
    if meta.file_type().is_symlink() {
        changes.push(content_change(
            OverlayPathChangeKind::Symlink,
            &relative_to_string(&rel),
            entry,
        )?);
    } else if meta.is_file() {
        changes.push(content_change(
            OverlayPathChangeKind::Write,
            &relative_to_string(&rel),
            entry,
        )?);
    }
    Ok(())
}

fn content_change(
    kind: OverlayPathChangeKind,
    path: &str,
    entry: &Path,
) -> Result<OverlayPathChange> {
    OverlayPathChange::new(
        path,
        kind,
        Some(entry.to_string_lossy().into_owned()),
        Some(content_hash(entry, kind == OverlayPathChangeKind::Symlink)?),
    )
}

fn normalize_overlay_path(path: &str, allow_root: bool) -> Result<String> {
    let raw = path.replace('\\', "/");
    let raw = raw.trim();
    if allow_root && (raw.is_empty() || raw == ".") {
        return Ok(String::new());
    }
    Ok(LayerPath::parse(raw)?.as_str().to_owned())
}

fn relative_path(root: &Path, entry: &Path) -> Result<PathBuf> {
    entry
        .strip_prefix(root)
        .map(Path::to_path_buf)
        .map_err(|err| OverlayError::InvalidPathChange(err.to_string()))
}

fn relative_overlay_path(root: &Path, entry: &Path) -> Result<String> {
    relative_path(root, entry).map(|path| relative_to_string(&path))
}

fn relative_to_string(path: &Path) -> String {
    path.components()
        .map(|component| component.as_os_str().to_string_lossy())
        .collect::<Vec<_>>()
        .join("/")
}

fn is_whiteout_marker(name: &str) -> bool {
    name.starts_with(WHITEOUT_PREFIX) && name != OPAQUE_MARKER && name.len() > WHITEOUT_PREFIX.len()
}

fn whiteout_target(rel: &Path) -> PathBuf {
    let name = rel
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or_default();
    let target_name = &name[WHITEOUT_PREFIX.len()..];
    rel.parent()
        .filter(|parent| !parent.as_os_str().is_empty())
        .map_or_else(
            || PathBuf::from(target_name),
            |parent| parent.join(target_name),
        )
}

fn is_overlay_whiteout(entry: &Path) -> Result<bool> {
    let meta = std::fs::symlink_metadata(entry).map_err(OverlayError::Capture)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::{FileTypeExt, MetadataExt};
        if meta.file_type().is_char_device() && meta.rdev() == 0 {
            return Ok(true);
        }
    }
    Ok(meta.is_file() && meta.len() == 0 && xattr_value(entry, "user.overlay.whiteout")?.is_some())
}

fn has_overlay_opaque_xattr(entry: &Path) -> bool {
    matches!(xattr_value(entry, "trusted.overlay.opaque"), Ok(Some(value)) if value == b"y")
        || matches!(xattr_value(entry, "user.overlay.opaque"), Ok(Some(value)) if value == b"y")
}

fn content_hash(path: &Path, symlink: bool) -> Result<String> {
    let data = if symlink {
        std::fs::read_link(path)
            .map_err(OverlayError::Capture)?
            .to_string_lossy()
            .into_owned()
            .into_bytes()
    } else {
        std::fs::read(path).map_err(OverlayError::Capture)?
    };
    let mut hasher = Sha256::new();
    hasher.update(data);
    Ok(format!("{:x}", hasher.finalize()))
}

#[cfg(target_os = "linux")]
fn xattr_value(path: &Path, name: &str) -> Result<Option<Vec<u8>>> {
    use std::ffi::CString;
    use std::os::unix::ffi::OsStrExt;

    let path = CString::new(path.as_os_str().as_bytes())
        .map_err(|err| OverlayError::InvalidPathChange(err.to_string()))?;
    let name =
        CString::new(name).map_err(|err| OverlayError::InvalidPathChange(err.to_string()))?;
    // SAFETY: `path` and `name` are live NUL-terminated C strings, and a null
    // value pointer with size 0 is the documented probe form for getxattr.
    let len = unsafe { libc::getxattr(path.as_ptr(), name.as_ptr(), std::ptr::null_mut(), 0) };
    if len < 0 {
        let err = std::io::Error::last_os_error();
        return match err.raw_os_error() {
            Some(libc::ENODATA | libc::EOPNOTSUPP) => Ok(None),
            _ => Err(OverlayError::Capture(err)),
        };
    }
    let len = usize::try_from(len).map_err(|_| {
        OverlayError::Capture(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            "xattr length does not fit usize",
        ))
    })?;
    let mut buffer = vec![0u8; len];
    // SAFETY: `buffer` is allocated with the size returned by the first
    // getxattr call, and its mutable pointer remains valid for `buffer.len()`
    // bytes for the duration of this FFI call.
    let read = unsafe {
        libc::getxattr(
            path.as_ptr(),
            name.as_ptr(),
            buffer.as_mut_ptr().cast(),
            buffer.len(),
        )
    };
    if read < 0 {
        return Err(OverlayError::Capture(std::io::Error::last_os_error()));
    }
    let read = usize::try_from(read).map_err(|_| {
        OverlayError::Capture(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            "xattr read length does not fit usize",
        ))
    })?;
    buffer.truncate(read);
    Ok(Some(buffer))
}

#[cfg(not(target_os = "linux"))]
// Keep the same fallible helper signature as Linux so whiteout/opaque detection
// call sites stay cfg-free; xattrs simply do not contribute off Linux.
#[expect(
    clippy::unnecessary_wraps,
    reason = "non-Linux parity keeps the Linux fallible helper signature"
)]
const fn xattr_value(_path: &Path, _name: &str) -> Result<Option<Vec<u8>>> {
    Ok(None)
}

#[cfg(test)]
mod tests {
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
}
