//! Setns file-op body: `setns` into the session's user+mount namespaces, then
//! read or write one regular file at `workspace_root/rel` through the mounted
//! overlay. Path walking is fd-relative with no-follow parent opens, so a
//! symlink component can never redirect the operation out of the workspace tree.

use std::io::{Read, Write};
use std::os::fd::OwnedFd;
use std::path::Path;

use base64::engine::general_purpose::STANDARD;
use base64::Engine;
use rustix::fs::{
    fchmod, fsync, mkdirat, open, openat, renameat, statat, unlinkat, AtFlags, FileType, Mode,
    OFlags, Stat,
};
use rustix::io::Errno;

use crate::runner::file_op::{
    decode_op, FileRunnerEntryKind, FileRunnerError, FileRunnerOp, FileRunnerResult,
};
use crate::runner::protocol::NamespaceRunnerRequest;
use crate::runner::RunnerError;

const DIR_MODE: u32 = 0o755;
const DEFAULT_FILE_MODE: u32 = 0o644;
const PERM_MASK: u32 = 0o7777;

pub(crate) fn run_file_op_setns(
    request: &NamespaceRunnerRequest,
) -> Result<FileRunnerResult, FileRunnerError> {
    let op = decode_op(request)?;
    super::namespaces::setns_user_mnt(request, "setns file op").map_err(runner_error_to_io)?;
    perform(&request.workspace_root, op)
}

fn perform(root: &Path, op: FileRunnerOp) -> Result<FileRunnerResult, FileRunnerError> {
    match op {
        FileRunnerOp::ReadWindow {
            rel,
            offset,
            limit,
            output_cap,
        } => read_window(root, &rel, offset, limit, output_cap),
        FileRunnerOp::ReadFile { rel, max_bytes } => read_file(root, &rel, max_bytes),
        FileRunnerOp::Write { rel, content } => write(root, &rel, &content),
    }
}

fn read_window(
    root: &Path,
    rel: &str,
    offset: u64,
    limit: usize,
    output_cap: usize,
) -> Result<FileRunnerResult, FileRunnerError> {
    let (parents, name) = split_rel(rel)?;
    let Some(parent) = walk_parents(open_root(root, rel)?, &parents, rel, false)? else {
        return Ok(absent_window(offset));
    };
    let Some(stat) = classify(&parent, name, rel)? else {
        return Ok(absent_window(offset));
    };
    require_regular(&stat)?;
    let total_bytes = file_size(&stat);
    let bytes = read_regular(&parent, name, rel, usize::MAX)?;
    let text = std::str::from_utf8(&bytes).map_err(|_| FileRunnerError::NotUtf8)?;
    let window = window_text(text, offset, limit, output_cap)
        .ok_or(FileRunnerError::OutputTooLarge { limit: output_cap })?;
    Ok(FileRunnerResult::ReadWindow {
        existed: true,
        content: window.content,
        start_line: window.start_line,
        num_lines: window.num_lines,
        total_lines: window.total_lines,
        bytes_read: window.bytes_read,
        total_bytes,
        next_offset: window.next_offset,
        truncated: window.truncated,
    })
}

fn read_file(
    root: &Path,
    rel: &str,
    max_bytes: usize,
) -> Result<FileRunnerResult, FileRunnerError> {
    let (parents, name) = split_rel(rel)?;
    let Some(parent) = walk_parents(open_root(root, rel)?, &parents, rel, false)? else {
        return Ok(absent_file());
    };
    let Some(stat) = classify(&parent, name, rel)? else {
        return Ok(absent_file());
    };
    require_regular(&stat)?;
    let total_bytes = file_size(&stat);
    if total_bytes > max_bytes as u64 {
        return Err(FileRunnerError::FileTooLarge {
            size: total_bytes,
            limit: max_bytes,
        });
    }
    let bytes = read_regular(&parent, name, rel, max_bytes)?;
    Ok(FileRunnerResult::ReadFile {
        existed: true,
        bytes_b64: STANDARD.encode(&bytes),
        total_bytes,
    })
}

fn write(root: &Path, rel: &str, content: &str) -> Result<FileRunnerResult, FileRunnerError> {
    let (parents, name) = split_rel(rel)?;
    let parent = walk_parents(open_root(root, rel)?, &parents, rel, true)?
        .ok_or_else(|| io_message(rel, "missing parent after create"))?;
    let existing = classify(&parent, name, rel)?;
    let (existed, mode) = match existing {
        Some(stat) => {
            require_regular(&stat)?;
            (true, stat.st_mode & PERM_MASK)
        }
        None => (false, DEFAULT_FILE_MODE),
    };
    let bytes = content.as_bytes();
    let tmp_name = format!(".{name}.tmp.{}", std::process::id());
    write_atomic(&parent, &tmp_name, name, rel, bytes, mode)?;
    Ok(FileRunnerResult::Write {
        existed,
        bytes_written: bytes.len(),
    })
}

fn write_atomic(
    parent: &OwnedFd,
    tmp_name: &str,
    name: &str,
    rel: &str,
    bytes: &[u8],
    mode: u32,
) -> Result<(), FileRunnerError> {
    let tmp_fd = openat(
        parent,
        tmp_name,
        OFlags::WRONLY | OFlags::CREATE | OFlags::EXCL | OFlags::NOFOLLOW | OFlags::CLOEXEC,
        Mode::from_raw_mode(mode),
    )
    .map_err(|err| io_errno(rel, err))?;
    let result = write_tmp(&tmp_fd, bytes, mode)
        .and_then(|()| renameat(parent, tmp_name, parent, name).map_err(|err| io_errno(rel, err)));
    if result.is_err() {
        let _ = unlinkat(parent, tmp_name, AtFlags::empty());
        return result;
    }
    let _ = fsync(parent);
    Ok(())
}

fn write_tmp(tmp_fd: &OwnedFd, bytes: &[u8], mode: u32) -> Result<(), FileRunnerError> {
    fchmod(tmp_fd, Mode::from_raw_mode(mode)).map_err(|err| io_errno("", err))?;
    let mut file = std::fs::File::from(
        tmp_fd
            .try_clone()
            .map_err(|err| io_message("", &err.to_string()))?,
    );
    file.write_all(bytes)
        .map_err(|err| io_message("", &err.to_string()))?;
    file.sync_all()
        .map_err(|err| io_message("", &err.to_string()))?;
    Ok(())
}

fn open_root(root: &Path, rel: &str) -> Result<OwnedFd, FileRunnerError> {
    open(
        root,
        OFlags::RDONLY | OFlags::DIRECTORY | OFlags::CLOEXEC,
        Mode::empty(),
    )
    .map_err(|err| io_errno(rel, err))
}

/// Walk `parents` under `root` with fd-relative no-follow directory opens. When
/// `create` is set, missing components are created; otherwise a missing
/// component yields `None` (the target is absent). A symlink or non-directory
/// component is rejected.
fn walk_parents(
    root: OwnedFd,
    parents: &[&str],
    rel: &str,
    create: bool,
) -> Result<Option<OwnedFd>, FileRunnerError> {
    let mut dir = root;
    for component in parents {
        match open_dir(&dir, component) {
            Ok(next) => dir = next,
            Err(Errno::NOENT) if create => {
                match mkdirat(&dir, *component, Mode::from_raw_mode(DIR_MODE)) {
                    Ok(()) | Err(Errno::EXIST) => {}
                    Err(err) => return Err(io_errno(rel, err)),
                }
                dir = open_dir(&dir, component).map_err(|err| io_errno(rel, err))?;
            }
            Err(Errno::NOENT) => return Ok(None),
            Err(Errno::LOOP) => {
                return Err(FileRunnerError::NotRegular {
                    kind: FileRunnerEntryKind::Symlink,
                })
            }
            Err(Errno::NOTDIR) => {
                return Err(FileRunnerError::NotRegular {
                    kind: FileRunnerEntryKind::Other,
                })
            }
            Err(err) => return Err(io_errno(rel, err)),
        }
    }
    Ok(Some(dir))
}

fn open_dir(dir: &OwnedFd, name: &str) -> Result<OwnedFd, Errno> {
    openat(
        dir,
        name,
        OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW | OFlags::CLOEXEC,
        Mode::empty(),
    )
}

fn classify(parent: &OwnedFd, name: &str, rel: &str) -> Result<Option<Stat>, FileRunnerError> {
    match statat(parent, name, AtFlags::SYMLINK_NOFOLLOW) {
        Ok(stat) => Ok(Some(stat)),
        Err(Errno::NOENT) => Ok(None),
        Err(err) => Err(io_errno(rel, err)),
    }
}

fn read_regular(
    parent: &OwnedFd,
    name: &str,
    rel: &str,
    max_bytes: usize,
) -> Result<Vec<u8>, FileRunnerError> {
    let fd = openat(
        parent,
        name,
        OFlags::RDONLY | OFlags::NOFOLLOW | OFlags::CLOEXEC | OFlags::NONBLOCK,
        Mode::empty(),
    )
    .map_err(|err| match err {
        Errno::LOOP => FileRunnerError::NotRegular {
            kind: FileRunnerEntryKind::Symlink,
        },
        other => io_errno(rel, other),
    })?;
    let limit = max_bytes.saturating_add(1) as u64;
    let mut bytes = Vec::new();
    std::fs::File::from(fd)
        .take(limit)
        .read_to_end(&mut bytes)
        .map_err(|err| io_message(rel, &err.to_string()))?;
    if bytes.len() > max_bytes {
        return Err(FileRunnerError::FileTooLarge {
            size: bytes.len() as u64,
            limit: max_bytes,
        });
    }
    Ok(bytes)
}

fn require_regular(stat: &Stat) -> Result<(), FileRunnerError> {
    match FileType::from_raw_mode(stat.st_mode) {
        FileType::RegularFile => Ok(()),
        FileType::Directory => Err(FileRunnerError::NotRegular {
            kind: FileRunnerEntryKind::Directory,
        }),
        FileType::Symlink => Err(FileRunnerError::NotRegular {
            kind: FileRunnerEntryKind::Symlink,
        }),
        _ => Err(FileRunnerError::NotRegular {
            kind: FileRunnerEntryKind::Other,
        }),
    }
}

fn file_size(stat: &Stat) -> u64 {
    u64::try_from(stat.st_size).unwrap_or(0)
}

fn split_rel(rel: &str) -> Result<(Vec<&str>, &str), FileRunnerError> {
    let components: Vec<&str> = rel
        .split('/')
        .filter(|component| !component.is_empty() && *component != ".")
        .collect();
    if components.is_empty() || components.contains(&"..") {
        return Err(io_message(rel, "invalid path"));
    }
    match components.split_last() {
        Some((name, parents)) => Ok((parents.to_vec(), name)),
        None => Err(io_message(rel, "invalid path")),
    }
}

fn absent_window(offset: u64) -> FileRunnerResult {
    FileRunnerResult::ReadWindow {
        existed: false,
        content: String::new(),
        start_line: offset.max(1),
        num_lines: 0,
        total_lines: 0,
        bytes_read: 0,
        total_bytes: 0,
        next_offset: None,
        truncated: false,
    }
}

fn absent_file() -> FileRunnerResult {
    FileRunnerResult::ReadFile {
        existed: false,
        bytes_b64: String::new(),
        total_bytes: 0,
    }
}

fn io_errno(path: &str, err: Errno) -> FileRunnerError {
    io_message(path, &err.to_string())
}

fn io_message(path: &str, message: &str) -> FileRunnerError {
    FileRunnerError::Io {
        path: path.to_owned(),
        message: message.to_owned(),
    }
}

fn runner_error_to_io(err: RunnerError) -> FileRunnerError {
    FileRunnerError::Io {
        path: String::new(),
        message: err.to_string(),
    }
}

struct TextWindow {
    content: String,
    start_line: u64,
    num_lines: usize,
    total_lines: u64,
    bytes_read: usize,
    next_offset: Option<u64>,
    truncated: bool,
}

fn window_text(raw: &str, offset: u64, limit: usize, output_cap: usize) -> Option<TextWindow> {
    let normalized = normalize_text(raw);
    let lines = split_lines(&normalized);
    let total_lines = lines.len() as u64;
    let start_index = offset.saturating_sub(1) as usize;
    let selected: Vec<&str> = lines
        .iter()
        .skip(start_index)
        .take(limit)
        .copied()
        .collect();
    let content = selected.join("\n");
    let bytes_read = content.len();
    if bytes_read > output_cap {
        return None;
    }
    let next_index = start_index.saturating_add(selected.len());
    let next_offset = (next_index < lines.len()).then(|| next_index as u64 + 1);
    Some(TextWindow {
        content,
        start_line: offset.max(1),
        num_lines: selected.len(),
        total_lines,
        bytes_read,
        next_offset,
        truncated: next_offset.is_some(),
    })
}

fn normalize_text(raw: &str) -> String {
    let without_bom = raw.strip_prefix('\u{feff}').unwrap_or(raw);
    without_bom.replace("\r\n", "\n").replace('\r', "\n")
}

fn split_lines(text: &str) -> Vec<&str> {
    if text.is_empty() {
        return Vec::new();
    }
    let without_trailing = text.strip_suffix('\n').unwrap_or(text);
    if without_trailing.is_empty() {
        return vec![""];
    }
    without_trailing.split('\n').collect()
}
