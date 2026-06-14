//! Layer changes captured from a snapshot overlay upperdir.
//!
//! Capture walks ONLY the overlay `upperdir`: capture + publish is one atomic
//! unit per op, so a consumer never observes a partial write set. Other agents
//! never see a half-captured upperdir.

use std::collections::HashSet;
use std::io::{self, Read};
#[cfg(unix)]
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};

use thiserror::Error;

use crate::{CasError, LayerChange, LayerPath};

const WHITEOUT_PREFIX: &str = ".wh.";
const OPAQUE_MARKER: &str = ".wh..wh..opq";
const MAX_CAPTURE_FILE_BYTES: usize = 8 * 1024 * 1024;

/// Failures raised while capturing layer changes from an overlay upperdir.
#[derive(Debug, Error)]
#[non_exhaustive]
pub enum CaptureError {
    /// An upper-dir walk / capture I/O error.
    #[error("upperdir capture failed at {path}: {source}")]
    Capture {
        /// Path whose metadata, directory entries, xattrs, content, or link
        /// target could not be read.
        path: PathBuf,
        #[source]
        source: io::Error,
    },

    /// A captured overlay path did not normalize to a valid relative layer path.
    #[error(transparent)]
    Path(#[from] CasError),

    /// A captured overlay path could not be expressed as a layer path.
    #[error("invalid overlay path change: {0}")]
    InvalidPathChange(String),
}

impl CaptureError {
    fn capture(path: impl Into<PathBuf>, source: io::Error) -> Self {
        Self::Capture {
            path: path.into(),
            source,
        }
    }

    #[must_use]
    pub fn failing_path(&self) -> Option<&Path> {
        match self {
            Self::Capture { path, .. } => Some(path.as_path()),
            _ => None,
        }
    }
}

/// Crate result alias for upperdir capture.
pub type Result<T> = std::result::Result<T, CaptureError>;

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct CaptureStats {
    pub files: u64,
    pub dirs: u64,
    pub symlinks: u64,
    pub bytes: u64,
    pub truncated: bool,
    pub read_error_count: u64,
    pub first_error_path: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct CapturedUpperdir {
    pub changes: Vec<LayerChange>,
    pub stats: CaptureStats,
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
/// Returns [`CaptureError`] when upperdir traversal, path normalization, xattr
/// probing, or content/link-target reads fail.
pub fn capture_upperdir(upperdir: &Path) -> Result<Vec<LayerChange>> {
    Ok(capture_upperdir_with_stats(upperdir)?.changes)
}

/// Walk the overlay `upperdir` once, returning both the captured write set and
/// resource stats counted during that capture walk.
///
/// # Errors
///
/// Returns [`CaptureError`] when upperdir traversal, path normalization, xattr
/// probing, or content/link-target reads fail.
pub fn capture_upperdir_with_stats(upperdir: &Path) -> Result<CapturedUpperdir> {
    std::fs::create_dir_all(upperdir).map_err(|err| CaptureError::capture(upperdir, err))?;
    let mut emitted_opaque_dirs = HashSet::new();
    let mut changes = Vec::new();
    let mut stats = CaptureStats {
        dirs: 1,
        ..CaptureStats::default()
    };
    walk_upperdir(
        upperdir,
        upperdir,
        &mut emitted_opaque_dirs,
        &mut changes,
        &mut stats,
    )?;
    Ok(CapturedUpperdir { changes, stats })
}

fn walk_upperdir(
    root: &Path,
    dir: &Path,
    emitted_opaque_dirs: &mut HashSet<String>,
    changes: &mut Vec<LayerChange>,
    stats: &mut CaptureStats,
) -> Result<()> {
    let mut entries = std::fs::read_dir(dir)
        .map_err(|err| CaptureError::capture(dir, err))?
        .collect::<std::result::Result<Vec<_>, _>>()
        .map_err(|err| CaptureError::capture(dir, err))?;
    entries.sort_by_key(std::fs::DirEntry::file_name);

    let mut dirs = Vec::new();
    let mut files = Vec::new();
    for entry in entries {
        let path = entry.path();
        let meta =
            std::fs::symlink_metadata(&path).map_err(|err| CaptureError::capture(&path, err))?;
        let file_type = meta.file_type();
        if file_type.is_dir() {
            stats.dirs = stats.dirs.saturating_add(1);
            dirs.push(path);
        } else {
            record_file_stats(stats, &meta);
            files.push((path, meta));
        }
    }

    for (entry, meta) in files {
        capture_file_entry(root, &entry, &meta, emitted_opaque_dirs, changes)?;
    }
    for entry in dirs {
        if has_overlay_opaque_xattr(&entry) {
            let opaque_path = relative_to_string(&relative_path(root, &entry)?)?;
            push_opaque_dir(opaque_path, emitted_opaque_dirs, changes)?;
        }
        walk_upperdir(root, &entry, emitted_opaque_dirs, changes, stats)?;
    }
    Ok(())
}

fn capture_file_entry(
    root: &Path,
    entry: &Path,
    meta: &std::fs::Metadata,
    emitted_opaque_dirs: &mut HashSet<String>,
    changes: &mut Vec<LayerChange>,
) -> Result<()> {
    let rel = relative_path(root, entry)?;
    let name = entry
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or_default();
    if name == OPAQUE_MARKER {
        let opaque_path = rel
            .parent()
            .map(relative_to_string)
            .transpose()?
            .unwrap_or_default();
        push_opaque_dir(opaque_path, emitted_opaque_dirs, changes)?;
        return Ok(());
    }
    if is_whiteout_marker(name) {
        let target = whiteout_target(&rel);
        changes.push(delete_change(&relative_to_string(&target)?)?);
        return Ok(());
    }
    if is_overlay_whiteout(entry, meta)? {
        changes.push(delete_change(&relative_to_string(&rel)?)?);
        return Ok(());
    }
    if meta.file_type().is_symlink() {
        changes.push(symlink_change(&relative_to_string(&rel)?, entry)?);
    } else if meta.is_file() {
        changes.push(write_change(&relative_to_string(&rel)?, entry, meta)?);
    }
    Ok(())
}

fn record_file_stats(stats: &mut CaptureStats, meta: &std::fs::Metadata) {
    let file_type = meta.file_type();
    if file_type.is_symlink() {
        stats.symlinks = stats.symlinks.saturating_add(1);
    } else if file_type.is_file() {
        stats.files = stats.files.saturating_add(1);
        stats.bytes = stats.bytes.saturating_add(meta.len());
    }
}

fn push_opaque_dir(
    path: String,
    emitted_opaque_dirs: &mut HashSet<String>,
    changes: &mut Vec<LayerChange>,
) -> Result<()> {
    if emitted_opaque_dirs.insert(path.clone()) {
        changes.push(LayerChange::OpaqueDir {
            path: layer_path(&path)?,
        });
    }
    Ok(())
}

fn delete_change(path: &str) -> Result<LayerChange> {
    Ok(LayerChange::Delete {
        path: layer_path(path)?,
    })
}

fn write_change(path: &str, entry: &Path, meta: &std::fs::Metadata) -> Result<LayerChange> {
    write_change_limited(path, entry, meta, MAX_CAPTURE_FILE_BYTES)
}

fn write_change_limited(
    path: &str,
    entry: &Path,
    meta: &std::fs::Metadata,
    max_bytes: usize,
) -> Result<LayerChange> {
    Ok(LayerChange::Write {
        path: layer_path(path)?,
        content: read_regular_file(entry, meta, max_bytes)?,
    })
}

fn read_regular_file(
    entry: &Path,
    expected_meta: &std::fs::Metadata,
    max_bytes: usize,
) -> Result<Vec<u8>> {
    ensure_capture_file_size(entry, expected_meta.len(), max_bytes)?;
    let file =
        open_regular_file_no_follow(entry).map_err(|err| CaptureError::capture(entry, err))?;
    let actual_meta = file
        .metadata()
        .map_err(|err| CaptureError::capture(entry, err))?;
    if !actual_meta.is_file() || !same_file(expected_meta, &actual_meta) {
        return Err(changed_during_capture(entry));
    }
    ensure_capture_file_size(entry, actual_meta.len(), max_bytes)?;

    let mut content = Vec::new();
    let limit = u64::try_from(max_bytes)
        .unwrap_or(u64::MAX)
        .saturating_add(1);
    file.take(limit)
        .read_to_end(&mut content)
        .map_err(|err| CaptureError::capture(entry, err))?;
    if content.len() > max_bytes {
        return Err(capture_file_too_large(
            entry,
            u64::try_from(content.len()).unwrap_or(u64::MAX),
            max_bytes,
        ));
    }
    Ok(content)
}

fn ensure_capture_file_size(entry: &Path, size: u64, max_bytes: usize) -> Result<()> {
    let max = u64::try_from(max_bytes).unwrap_or(u64::MAX);
    if size > max {
        return Err(capture_file_too_large(entry, size, max_bytes));
    }
    Ok(())
}

#[cfg(unix)]
fn open_regular_file_no_follow(entry: &Path) -> io::Result<std::fs::File> {
    use rustix::fs::{Mode, OFlags};

    rustix::fs::open(
        entry,
        OFlags::RDONLY | OFlags::NOFOLLOW | OFlags::CLOEXEC,
        Mode::empty(),
    )
    .map(std::fs::File::from)
    .map_err(io::Error::from)
}

#[cfg(not(unix))]
fn open_regular_file_no_follow(entry: &Path) -> io::Result<std::fs::File> {
    std::fs::File::open(entry)
}

#[cfg(unix)]
fn same_file(left: &std::fs::Metadata, right: &std::fs::Metadata) -> bool {
    left.dev() == right.dev() && left.ino() == right.ino()
}

#[cfg(not(unix))]
fn same_file(_left: &std::fs::Metadata, _right: &std::fs::Metadata) -> bool {
    true
}

fn changed_during_capture(entry: &Path) -> CaptureError {
    CaptureError::capture(
        entry,
        io::Error::new(
            io::ErrorKind::InvalidData,
            "overlay regular file changed during capture",
        ),
    )
}

fn capture_file_too_large(entry: &Path, size: u64, max_bytes: usize) -> CaptureError {
    CaptureError::capture(
        entry,
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!("overlay regular file too large: {size} > {max_bytes} bytes"),
        ),
    )
}

fn symlink_change(path: &str, entry: &Path) -> Result<LayerChange> {
    Ok(LayerChange::Symlink {
        path: layer_path(path)?,
        source_path: path_string(
            &std::fs::read_link(entry).map_err(|err| CaptureError::capture(entry, err))?,
        )?,
    })
}

fn layer_path(path: &str) -> Result<LayerPath> {
    LayerPath::parse(path).map_err(CaptureError::Path)
}

fn relative_path(root: &Path, entry: &Path) -> Result<PathBuf> {
    entry
        .strip_prefix(root)
        .map(Path::to_path_buf)
        .map_err(|err| CaptureError::InvalidPathChange(err.to_string()))
}

fn relative_to_string(path: &Path) -> Result<String> {
    let mut parts = Vec::new();
    for component in path.components() {
        parts.push(path_component_string(component.as_os_str())?);
    }
    Ok(parts.join("/"))
}

fn path_string(path: &Path) -> Result<String> {
    path.to_str().map(str::to_owned).ok_or_else(|| {
        CaptureError::InvalidPathChange(format!(
            "overlay path is not valid UTF-8: {}",
            path.display()
        ))
    })
}

fn path_component_string(component: &std::ffi::OsStr) -> Result<String> {
    component.to_str().map(str::to_owned).ok_or_else(|| {
        let bytes = component.as_encoded_bytes();
        CaptureError::InvalidPathChange(format!(
            "overlay path component is not valid UTF-8: {bytes:?}"
        ))
    })
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

fn is_overlay_whiteout(entry: &Path, meta: &std::fs::Metadata) -> Result<bool> {
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

#[cfg(target_os = "linux")]
fn xattr_value(path: &Path, name: &str) -> Result<Option<Vec<u8>>> {
    use rustix::io::Errno;

    let mut buffer = vec![0_u8; 64];
    loop {
        match rustix::fs::lgetxattr(path, name, &mut buffer) {
            Ok(len) => {
                buffer.truncate(len);
                return Ok(Some(buffer));
            }
            Err(Errno::RANGE) => buffer.resize(buffer.len() * 2, 0),
            Err(Errno::NODATA | Errno::OPNOTSUPP) => return Ok(None),
            Err(err) => return Err(CaptureError::capture(path, std::io::Error::from(err))),
        }
    }
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
#[path = "../tests/unit/capture.rs"]
mod tests;
