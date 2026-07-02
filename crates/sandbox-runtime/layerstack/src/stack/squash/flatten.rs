//! Pure fold of a squash block's layer directories into one staging tree.
//!
//! Newest-wins per path/subtree with `MergedView`-equivalent semantics:
//! every surviving directory gets an explicit entry, whiteout winners are
//! re-emitted through [`write_kernel_whiteout`], and a merged-directory run
//! terminated inside the block (whiteout, non-directory, or opaque) re-emits
//! as an opaque directory carrying both the `.wh..wh..opq` marker (read by
//! `MergedView`/capture) and the `user.overlay.opaque` xattr (the only
//! encoding the kernel honors on lower layers). Regular-file winners are
//! hardlinked from the immutable source. Source walks are fd-relative and
//! never follow symlinks; output writes are path-based inside the
//! freshly-created staging tree.

use std::collections::BTreeMap;
use std::ffi::{OsStr, OsString};
use std::fs;
use std::os::fd::{AsFd, OwnedFd};
use std::os::unix::ffi::{OsStrExt, OsStringExt};
use std::os::unix::fs::{symlink, PermissionsExt};
use std::path::{Path, PathBuf};

use rustix::fs::{AtFlags, Mode, OFlags, XattrFlags};
use rustix::io::Errno;

use crate::error::LayerStackError;
use crate::whiteout::{
    write_kernel_whiteout, LOGICAL_WHITEOUT_PREFIX, OPAQUE_MARKER, TRUSTED_OVERLAY_OPAQUE_XATTR,
    TRUSTED_OVERLAY_WHITEOUT_XATTR, USER_OVERLAY_OPAQUE_XATTR, USER_OVERLAY_WHITEOUT_XATTR,
};

/// Fold `sources_newest_first` (a squash block's layer directories, mount
/// priority order) into `staging_dir`.
///
/// # Errors
///
/// Returns [`LayerStackError`] when the block is smaller than two layers,
/// a source entry has an unsupported file type, or any filesystem operation
/// fails; the caller owns cleanup of the partially-written staging tree.
pub(crate) fn flatten_block_into(
    staging_dir: &Path,
    sources_newest_first: &[PathBuf],
) -> Result<(), LayerStackError> {
    if sources_newest_first.len() < 2 {
        return Err(LayerStackError::Storage(format!(
            "flatten requires a block of at least two source layers, got {}",
            sources_newest_first.len()
        )));
    }
    let mut roots = Vec::with_capacity(sources_newest_first.len());
    for source in sources_newest_first {
        roots.push(open_dir_abs(source)?);
    }
    fs::create_dir_all(staging_dir)?;
    let root_opaque = merge_dirs(staging_dir, &roots)?;
    if root_opaque {
        mark_opaque(staging_dir)?;
    }
    Ok(())
}

enum SourceEntry {
    Whiteout,
    File,
    Symlink,
    Dir { mode: u32 },
}

struct Winner {
    layer: usize,
    whiteout: bool,
}

/// Merge the participating layer directories (newest-first) into `out_dir`.
/// Returns whether an opaque cut was found, in which case the caller must
/// mask `out_dir` against layers below the block.
fn merge_dirs(out_dir: &Path, layers: &[OwnedFd]) -> Result<bool, LayerStackError> {
    let cut = opaque_cut(layers)?;
    let layers = match cut {
        Some(index) => &layers[..=index],
        None => layers,
    };
    for (name, winner) in collect_winners(layers)? {
        emit_winner(out_dir, layers, &name, &winner)?;
    }
    Ok(cut.is_some())
}

/// Newest occurrence per name across the participating layers; a logical
/// whiteout claim in the same layer as a real entry wins, matching
/// `MergedView::is_whiteouted` running before the entry lookup.
fn collect_winners(layers: &[OwnedFd]) -> Result<BTreeMap<OsString, Winner>, LayerStackError> {
    let mut first_dirent: BTreeMap<OsString, usize> = BTreeMap::new();
    let mut first_claim: BTreeMap<OsString, usize> = BTreeMap::new();
    for (index, layer) in layers.iter().enumerate() {
        for name in dir_names(layer)? {
            let bytes = name.as_bytes();
            if bytes == OPAQUE_MARKER.as_bytes() {
                continue;
            }
            if let Some(target) = logical_whiteout_target(bytes) {
                first_claim
                    .entry(OsString::from_vec(target.to_vec()))
                    .or_insert(index);
            } else {
                first_dirent.entry(name).or_insert(index);
            }
        }
    }
    let mut winners = BTreeMap::new();
    for (name, dirent_layer) in first_dirent {
        let claim_layer = first_claim.remove(&name);
        let winner = match claim_layer {
            Some(claim) if claim <= dirent_layer => Winner {
                layer: claim,
                whiteout: true,
            },
            _ => Winner {
                layer: dirent_layer,
                whiteout: false,
            },
        };
        winners.insert(name, winner);
    }
    for (name, claim_layer) in first_claim {
        winners.insert(
            name,
            Winner {
                layer: claim_layer,
                whiteout: true,
            },
        );
    }
    Ok(winners)
}

fn emit_winner(
    out_dir: &Path,
    layers: &[OwnedFd],
    name: &OsStr,
    winner: &Winner,
) -> Result<(), LayerStackError> {
    let out_path = out_dir.join(name);
    if winner.whiteout {
        return write_kernel_whiteout(&out_path);
    }
    let entry = classify_at(&layers[winner.layer], name)?
        .ok_or_else(|| flatten_error(name, "source entry vanished during flatten walk"))?;
    match entry {
        SourceEntry::Whiteout => write_kernel_whiteout(&out_path),
        SourceEntry::File => rustix::fs::linkat(
            &layers[winner.layer],
            name,
            rustix::fs::CWD,
            &out_path,
            AtFlags::empty(),
        )
        .map_err(|errno| flatten_errno(name, "linkat", errno)),
        SourceEntry::Symlink => {
            let target = rustix::fs::readlinkat(&layers[winner.layer], name, Vec::new())
                .map_err(|errno| flatten_errno(name, "readlinkat", errno))?;
            symlink(OsStr::from_bytes(target.as_bytes()), &out_path)?;
            Ok(())
        }
        SourceEntry::Dir { mode } => emit_merged_dir(&out_path, layers, name, winner.layer, mode),
    }
}

fn emit_merged_dir(
    out_path: &Path,
    layers: &[OwnedFd],
    name: &OsStr,
    newest: usize,
    mode: u32,
) -> Result<(), LayerStackError> {
    let mut members = vec![open_dir_at(&layers[newest], name)?];
    let mut terminated_in_block = false;
    for layer in &layers[newest + 1..] {
        if logical_claim_exists(layer, name)? {
            terminated_in_block = true;
            break;
        }
        match classify_at(layer, name)? {
            None => {}
            Some(SourceEntry::Dir { .. }) => members.push(open_dir_at(layer, name)?),
            Some(SourceEntry::Whiteout | SourceEntry::File | SourceEntry::Symlink) => {
                terminated_in_block = true;
                break;
            }
        }
    }
    fs::create_dir(out_path)?;
    let child_opaque = merge_dirs(out_path, &members)?;
    fs::set_permissions(out_path, fs::Permissions::from_mode(mode))?;
    if terminated_in_block || child_opaque {
        mark_opaque(out_path)?;
    }
    Ok(())
}

/// First participating layer whose directory carries an opaque signal —
/// the `.wh..wh..opq` marker (publish encoding) or an overlay opaque xattr
/// (prior-generation squash encoding); layers below it contribute nothing.
fn opaque_cut(layers: &[OwnedFd]) -> Result<Option<usize>, LayerStackError> {
    for (index, layer) in layers.iter().enumerate() {
        match rustix::fs::statat(layer, OPAQUE_MARKER, AtFlags::SYMLINK_NOFOLLOW) {
            Ok(_) => return Ok(Some(index)),
            Err(Errno::NOENT) => {}
            Err(errno) => return Err(flatten_errno(OsStr::new(OPAQUE_MARKER), "statat", errno)),
        }
        if fd_has_xattr_y(layer, USER_OVERLAY_OPAQUE_XATTR)
            || fd_has_xattr_y(layer, TRUSTED_OVERLAY_OPAQUE_XATTR)
        {
            return Ok(Some(index));
        }
    }
    Ok(None)
}

fn classify_at(layer: &OwnedFd, name: &OsStr) -> Result<Option<SourceEntry>, LayerStackError> {
    let stat = match rustix::fs::statat(layer, name, AtFlags::SYMLINK_NOFOLLOW) {
        Ok(stat) => stat,
        Err(Errno::NOENT) => return Ok(None),
        Err(errno) => return Err(flatten_errno(name, "statat", errno)),
    };
    let raw_mode = stat.st_mode as u32;
    let file_type = rustix::fs::FileType::from_raw_mode(raw_mode as rustix::fs::RawMode);
    let entry = match file_type {
        rustix::fs::FileType::CharacterDevice if stat.st_rdev as u64 == 0 => SourceEntry::Whiteout,
        rustix::fs::FileType::RegularFile => {
            if stat.st_size == 0 && file_is_xattr_whiteout(layer, name)? {
                SourceEntry::Whiteout
            } else {
                SourceEntry::File
            }
        }
        rustix::fs::FileType::Symlink => SourceEntry::Symlink,
        rustix::fs::FileType::Directory => SourceEntry::Dir {
            mode: raw_mode & 0o7777,
        },
        _ => {
            return Err(flatten_error(
                name,
                "unsupported source entry type in squash block",
            ))
        }
    };
    Ok(Some(entry))
}

fn file_is_xattr_whiteout(layer: &OwnedFd, name: &OsStr) -> Result<bool, LayerStackError> {
    let file = rustix::fs::openat(
        layer,
        name,
        OFlags::RDONLY | OFlags::NOFOLLOW | OFlags::CLOEXEC,
        Mode::empty(),
    )
    .map_err(|errno| flatten_errno(name, "openat", errno))?;
    Ok(fd_has_xattr(&file, TRUSTED_OVERLAY_WHITEOUT_XATTR)
        || fd_has_xattr(&file, USER_OVERLAY_WHITEOUT_XATTR))
}

/// Absent-on-any-error semantics, matching `whiteout::has_xattr` — the
/// classifier the rest of the stack already trusts for whiteout detection.
fn fd_has_xattr(fd: &OwnedFd, xattr: &str) -> bool {
    let mut value = [0_u8; 1];
    rustix::fs::fgetxattr(fd, xattr, &mut value).is_ok()
}

fn fd_has_xattr_y(fd: &OwnedFd, xattr: &str) -> bool {
    let mut value = [0_u8; 1];
    matches!(rustix::fs::fgetxattr(fd, xattr, &mut value), Ok(1) if value[0] == b'y')
}

fn mark_opaque(dir: &Path) -> Result<(), LayerStackError> {
    fs::write(dir.join(OPAQUE_MARKER), b"")?;
    rustix::fs::lsetxattr(dir, USER_OVERLAY_OPAQUE_XATTR, b"y", XattrFlags::empty()).map_err(
        |errno| {
            LayerStackError::Storage(format!(
                "flatten could not mark opaque dir {}: {errno}",
                dir.display()
            ))
        },
    )
}

fn dir_names(layer: &OwnedFd) -> Result<Vec<OsString>, LayerStackError> {
    let dir = rustix::fs::Dir::read_from(layer.as_fd())
        .map_err(|errno| flatten_errno(OsStr::new("."), "opendir", errno))?;
    let mut names = Vec::new();
    for entry in dir {
        let entry = entry.map_err(|errno| flatten_errno(OsStr::new("."), "readdir", errno))?;
        let bytes = entry.file_name().to_bytes();
        if bytes == b"." || bytes == b".." {
            continue;
        }
        names.push(OsString::from_vec(bytes.to_vec()));
    }
    Ok(names)
}

fn logical_whiteout_target(name: &[u8]) -> Option<&[u8]> {
    let prefix = LOGICAL_WHITEOUT_PREFIX.as_bytes();
    if name.len() > prefix.len() && name.starts_with(prefix) && name != OPAQUE_MARKER.as_bytes() {
        Some(&name[prefix.len()..])
    } else {
        None
    }
}

fn logical_claim_exists(layer: &OwnedFd, name: &OsStr) -> Result<bool, LayerStackError> {
    let mut claim = Vec::with_capacity(LOGICAL_WHITEOUT_PREFIX.len() + name.as_bytes().len());
    claim.extend_from_slice(LOGICAL_WHITEOUT_PREFIX.as_bytes());
    claim.extend_from_slice(name.as_bytes());
    let claim = OsString::from_vec(claim);
    match rustix::fs::statat(layer, claim.as_os_str(), AtFlags::SYMLINK_NOFOLLOW) {
        Ok(_) => Ok(true),
        Err(Errno::NOENT) => Ok(false),
        Err(errno) => Err(flatten_errno(name, "statat whiteout claim", errno)),
    }
}

fn open_dir_abs(path: &Path) -> Result<OwnedFd, LayerStackError> {
    rustix::fs::open(
        path,
        OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW | OFlags::CLOEXEC,
        Mode::empty(),
    )
    .map_err(|errno| {
        LayerStackError::Storage(format!(
            "flatten could not open source layer {}: {errno}",
            path.display()
        ))
    })
}

fn open_dir_at(layer: &OwnedFd, name: &OsStr) -> Result<OwnedFd, LayerStackError> {
    rustix::fs::openat(
        layer,
        name,
        OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW | OFlags::CLOEXEC,
        Mode::empty(),
    )
    .map_err(|errno| flatten_errno(name, "openat dir", errno))
}

fn flatten_errno(name: &OsStr, op: &str, errno: Errno) -> LayerStackError {
    LayerStackError::Storage(format!("flatten {op} {name:?}: {errno}"))
}

fn flatten_error(name: &OsStr, message: &str) -> LayerStackError {
    LayerStackError::Storage(format!("{message}: {name:?}"))
}
