//! In-memory tar builder for uploading daemon assets via the Docker Engine
//! `put_archive` endpoint. Entries are rooted at `/`, so the archive is
//! extracted with `path = "/"`.

use std::io;
use std::path::{Component, Path};

use bytes::Bytes;

const DAEMON_BINARY_MODE: u32 = 0o755;
const CONFIG_FILE_MODE: u32 = 0o644;
const DIRECTORY_MODE: u32 = 0o755;

/// Build a tar archive carrying the Linux daemon binary and config YAML at their
/// container paths, plus every parent directory entry they require.
pub fn build_install_archive(
    daemon_binary_container_path: &Path,
    daemon_binary: &[u8],
    config_container_path: &Path,
    config_yaml: &[u8],
) -> io::Result<Bytes> {
    let mut builder = tar::Builder::new(Vec::new());
    append_parent_dirs(&mut builder, daemon_binary_container_path)?;
    append_file(
        &mut builder,
        daemon_binary_container_path,
        daemon_binary,
        DAEMON_BINARY_MODE,
    )?;
    append_parent_dirs(&mut builder, config_container_path)?;
    append_file(
        &mut builder,
        config_container_path,
        config_yaml,
        CONFIG_FILE_MODE,
    )?;
    let inner = builder.into_inner()?;
    Ok(Bytes::from(inner))
}

fn append_file(
    builder: &mut tar::Builder<Vec<u8>>,
    container_path: &Path,
    data: &[u8],
    mode: u32,
) -> io::Result<()> {
    let mut header = tar::Header::new_gnu();
    header.set_entry_type(tar::EntryType::Regular);
    header.set_size(data.len() as u64);
    header.set_mode(mode);
    builder.append_data(&mut header, tar_entry_path(container_path), data)
}

fn append_parent_dirs(builder: &mut tar::Builder<Vec<u8>>, file_path: &Path) -> io::Result<()> {
    let Some(parent) = file_path.parent() else {
        return Ok(());
    };
    let mut accumulated = String::new();
    for component in parent.components() {
        if let Component::Normal(segment) = component {
            accumulated.push_str(&segment.to_string_lossy());
            accumulated.push('/');
            let mut header = tar::Header::new_gnu();
            header.set_entry_type(tar::EntryType::Directory);
            header.set_size(0);
            header.set_mode(DIRECTORY_MODE);
            builder.append_data(&mut header, &accumulated, io::empty())?;
        }
    }
    Ok(())
}

fn tar_entry_path(container_path: &Path) -> String {
    container_path
        .to_string_lossy()
        .trim_start_matches('/')
        .to_owned()
}
