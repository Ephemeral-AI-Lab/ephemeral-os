//! Layer changes captured from a snapshot overlay.
//!
//! Capture walks ONLY the overlay `upperdir`: capture + publish is one atomic
//! unit per op, so a consumer never observes a partial write set. Other agents
//! never see a half-captured upperdir.

use std::collections::HashSet;
use std::path::{Path, PathBuf};

use crate::{LayerChange, LayerPath, OverlayError, Result};

const WHITEOUT_PREFIX: &str = ".wh.";
const OPAQUE_MARKER: &str = ".wh..wh..opq";

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
/// probing, or content/link-target reads fail.
pub fn capture_upperdir(upperdir: &Path) -> Result<Vec<LayerChange>> {
    std::fs::create_dir_all(upperdir).map_err(OverlayError::Capture)?;
    let mut emitted_opaque_dirs = HashSet::new();
    let mut changes = Vec::new();
    walk_upperdir(upperdir, upperdir, &mut emitted_opaque_dirs, &mut changes)?;
    Ok(changes)
}

fn walk_upperdir(
    root: &Path,
    dir: &Path,
    emitted_opaque_dirs: &mut HashSet<String>,
    changes: &mut Vec<LayerChange>,
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
            push_opaque_dir(opaque_path, emitted_opaque_dirs, changes)?;
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
    changes: &mut Vec<LayerChange>,
) -> Result<()> {
    let rel = relative_path(root, entry)?;
    let name = entry
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or_default();
    if name == OPAQUE_MARKER {
        let opaque_path = rel.parent().map(relative_to_string).unwrap_or_default();
        push_opaque_dir(opaque_path, emitted_opaque_dirs, changes)?;
        return Ok(());
    }
    if is_whiteout_marker(name) {
        let target = whiteout_target(&rel);
        changes.push(delete_change(&relative_to_string(&target))?);
        return Ok(());
    }
    if is_overlay_whiteout(entry)? {
        changes.push(delete_change(&relative_to_string(&rel))?);
        return Ok(());
    }
    let meta = std::fs::symlink_metadata(entry).map_err(OverlayError::Capture)?;
    if meta.file_type().is_symlink() {
        changes.push(symlink_change(&relative_to_string(&rel), entry)?);
    } else if meta.is_file() {
        changes.push(write_change(&relative_to_string(&rel), entry)?);
    }
    Ok(())
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

fn write_change(path: &str, entry: &Path) -> Result<LayerChange> {
    Ok(LayerChange::Write {
        path: layer_path(path)?,
        content: std::fs::read(entry).map_err(OverlayError::Capture)?,
    })
}

fn symlink_change(path: &str, entry: &Path) -> Result<LayerChange> {
    Ok(LayerChange::Symlink {
        path: layer_path(path)?,
        source_path: std::fs::read_link(entry)
            .map_err(OverlayError::Capture)?
            .to_string_lossy()
            .into_owned(),
    })
}

fn layer_path(path: &str) -> Result<LayerPath> {
    LayerPath::parse(path).map_err(OverlayError::Path)
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
#[path = "../tests/unit/path_change.rs"]
mod tests;
