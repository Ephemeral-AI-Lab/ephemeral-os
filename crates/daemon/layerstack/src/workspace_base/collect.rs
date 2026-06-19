use std::io::{ErrorKind, Read};
use std::path::{Path, PathBuf};

use sha2::{Digest, Sha256};

use crate::error::LayerStackError;
use crate::model::hex_lower;

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) enum BaseEntry {
    Directory {
        path: String,
    },
    File {
        path: String,
        source_path: PathBuf,
        size: u64,
        content_hash: String,
    },
    Symlink {
        path: String,
        link_target: String,
    },
}

impl BaseEntry {
    pub(super) fn path(&self) -> &str {
        match self {
            Self::Directory { path } | Self::File { path, .. } | Self::Symlink { path, .. } => path,
        }
    }

    const fn kind(&self) -> &'static str {
        match self {
            Self::Directory { .. } => "directory",
            Self::File { .. } => "file",
            Self::Symlink { .. } => "symlink",
        }
    }
}

pub(super) fn collect_base_entries(
    workspace: &Path,
) -> Result<(Vec<BaseEntry>, String), LayerStackError> {
    let mut entries = Vec::new();
    let mut special = Vec::new();
    let mut unstable = Vec::new();
    collect_dir(
        workspace,
        workspace,
        &mut entries,
        &mut special,
        &mut unstable,
    )?;
    if !special.is_empty() || !unstable.is_empty() {
        special.sort();
        unstable.sort();
        return Err(LayerStackError::Storage(format!(
            "workspace base must be a full copy; special={} [{}], unstable={} [{}]",
            special.len(),
            format_path_sample(&special),
            unstable.len(),
            format_path_sample(&unstable)
        )));
    }
    entries.sort_by(|left, right| left.path().cmp(right.path()));
    let mut digest = Sha256::new();
    for entry in &entries {
        update_root_hash(&mut digest, entry);
    }
    Ok((entries, hex_lower(digest.finalize())))
}

pub(super) fn file_hash(path: &Path) -> Result<String, std::io::Error> {
    let mut file = std::fs::File::open(path)?;
    let mut digest = Sha256::new();
    let mut buffer = vec![0_u8; 1024 * 1024].into_boxed_slice();
    loop {
        let count = file.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        digest.update(&buffer[..count]);
    }
    Ok(hex_lower(digest.finalize()))
}

fn collect_dir(
    workspace: &Path,
    current: &Path,
    entries: &mut Vec<BaseEntry>,
    special: &mut Vec<String>,
    unstable: &mut Vec<String>,
) -> Result<(), LayerStackError> {
    let mut children = match std::fs::read_dir(current) {
        Ok(read_dir) => read_dir.collect::<Result<Vec<_>, _>>()?,
        Err(err) if err.kind() == ErrorKind::NotFound => {
            unstable.push(relative_path(workspace, current));
            return Ok(());
        }
        Err(err) => return Err(err.into()),
    };
    children.sort_by_key(std::fs::DirEntry::file_name);
    for child in children {
        let path = child.path();
        let rel = relative_path(workspace, &path);
        let meta = match std::fs::symlink_metadata(&path) {
            Ok(meta) => meta,
            Err(err) if err.kind() == ErrorKind::NotFound => {
                unstable.push(rel);
                continue;
            }
            Err(err) => return Err(err.into()),
        };
        let file_type = meta.file_type();
        if file_type.is_symlink() {
            match std::fs::read_link(&path) {
                Ok(target) => entries.push(BaseEntry::Symlink {
                    path: rel,
                    link_target: target.to_string_lossy().into_owned(),
                }),
                Err(_) => special.push(rel),
            }
        } else if meta.is_dir() {
            entries.push(BaseEntry::Directory { path: rel });
            collect_dir(workspace, &path, entries, special, unstable)?;
        } else if meta.is_file() {
            let content_hash = match file_hash(&path) {
                Ok(hash) => hash,
                Err(err) if err.kind() == ErrorKind::NotFound => {
                    unstable.push(rel);
                    continue;
                }
                Err(_) => {
                    special.push(rel);
                    continue;
                }
            };
            entries.push(BaseEntry::File {
                path: rel,
                source_path: path,
                size: meta.len(),
                content_hash,
            });
        } else {
            special.push(rel);
        }
    }
    Ok(())
}

fn update_root_hash(digest: &mut Sha256, entry: &BaseEntry) {
    digest.update(entry.kind().as_bytes());
    digest.update(b"\0");
    digest.update(entry.path().as_bytes());
    digest.update(b"\0");
    match entry {
        BaseEntry::File {
            size, content_hash, ..
        } => {
            digest.update(size.to_string().as_bytes());
            digest.update(b"\0");
            digest.update(content_hash.as_bytes());
        }
        BaseEntry::Symlink { link_target, .. } => {
            digest.update(link_target.as_bytes());
        }
        BaseEntry::Directory { .. } => {}
    }
    digest.update(b"\0");
}

fn relative_path(workspace: &Path, path: &Path) -> String {
    path.strip_prefix(workspace)
        .unwrap_or(path)
        .to_string_lossy()
        .replace('\\', "/")
}

fn format_path_sample(paths: &[String]) -> String {
    const LIMIT: usize = 5;
    let mut sample = paths.iter().take(LIMIT).cloned().collect::<Vec<_>>();
    if paths.len() > LIMIT {
        sample.push(format!("+{} more", paths.len() - LIMIT));
    }
    sample.join(", ")
}
