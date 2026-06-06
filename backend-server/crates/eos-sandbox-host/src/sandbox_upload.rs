//! `/eos`-only sandbox upload helpers.

use eos_types::SandboxId;

use crate::error::SandboxHostError;
use crate::provider::ProviderAdapter;

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct AbsoluteEosPath {
    inner: String,
}

impl AbsoluteEosPath {
    pub(crate) fn parse(path: impl AsRef<str>) -> Result<Self, SandboxHostError> {
        let inner = normalize_absolute_eos_path(path.as_ref())?;
        Ok(Self { inner })
    }

    pub(crate) fn as_str(&self) -> &str {
        &self.inner
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct SandboxUploadEntry {
    path: String,
    payload: Vec<u8>,
    mode: u32,
}

impl SandboxUploadEntry {
    pub(crate) fn file(
        path: impl AsRef<str>,
        payload: impl Into<Vec<u8>>,
        mode: u32,
    ) -> Result<Self, SandboxHostError> {
        let path = normalize_relative_tar_path(path.as_ref())?;
        Ok(Self {
            path,
            payload: payload.into(),
            mode,
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct SandboxUploadRequest {
    destination: AbsoluteEosPath,
    entries: Vec<SandboxUploadEntry>,
}

impl SandboxUploadRequest {
    pub(crate) fn new(
        destination: impl AsRef<str>,
        entries: Vec<SandboxUploadEntry>,
    ) -> Result<Self, SandboxHostError> {
        if entries.is_empty() {
            return Err(SandboxHostError::InvalidRequest(
                "sandbox upload requires at least one file entry".to_owned(),
            ));
        }
        Ok(Self {
            destination: AbsoluteEosPath::parse(destination)?,
            entries,
        })
    }
}

pub(crate) async fn upload_file_into_eos(
    adapter: &dyn ProviderAdapter,
    id: &SandboxId,
    destination: impl AsRef<str>,
    file_name: impl AsRef<str>,
    payload: &[u8],
    mode: u32,
) -> Result<(), SandboxHostError> {
    let entry = SandboxUploadEntry::file(file_name, payload.to_vec(), mode)?;
    upload_tree_into_eos(
        adapter,
        id,
        SandboxUploadRequest::new(destination, vec![entry])?,
    )
    .await
}

pub(crate) async fn upload_tree_into_eos(
    adapter: &dyn ProviderAdapter,
    id: &SandboxId,
    request: SandboxUploadRequest,
) -> Result<(), SandboxHostError> {
    let tar_stream = tar_entries(&request.entries)?;
    adapter
        .put_archive(id, &tar_stream, request.destination.as_str())
        .await
}

#[cfg(test)]
pub(crate) fn tar_file_at_path(
    name: &str,
    payload: &[u8],
    mode: u32,
) -> Result<Vec<u8>, SandboxHostError> {
    tar_entries(&[SandboxUploadEntry::file(name, payload.to_vec(), mode)?])
}

fn tar_entries(entries: &[SandboxUploadEntry]) -> Result<Vec<u8>, SandboxHostError> {
    if entries.is_empty() {
        return Err(SandboxHostError::InvalidRequest(
            "sandbox upload requires at least one file entry".to_owned(),
        ));
    }

    let mut builder = tar::Builder::new(Vec::new());
    for entry in entries {
        let mut header = tar::Header::new_gnu();
        header.set_entry_type(tar::EntryType::Regular);
        header.set_size(entry.payload.len() as u64);
        header.set_mtime(0);
        header.set_uid(0);
        header.set_gid(0);
        header.set_mode(entry.mode);
        header.set_cksum();
        builder.append_data(&mut header, entry.path.as_str(), entry.payload.as_slice())?;
    }
    builder.finish()?;
    Ok(builder.into_inner()?)
}

fn normalize_absolute_eos_path(path: &str) -> Result<String, SandboxHostError> {
    if path.as_bytes().contains(&0) {
        return invalid_path(path, "contains a nul byte");
    }
    if !path.starts_with('/') {
        return invalid_path(path, "is not absolute");
    }

    let components = normalize_components(path)?;
    if components.first().copied() != Some("eos") {
        return invalid_path(path, "is outside /eos");
    }

    Ok(format!("/{}", components.join("/")))
}

fn normalize_relative_tar_path(path: &str) -> Result<String, SandboxHostError> {
    if path.as_bytes().contains(&0) {
        return invalid_path(path, "contains a nul byte");
    }
    if path.starts_with('/') {
        return invalid_path(path, "is absolute");
    }

    let components = normalize_components(path)?;
    if components.is_empty() {
        return invalid_path(path, "is empty");
    }

    Ok(components.join("/"))
}

fn normalize_components(path: &str) -> Result<Vec<&str>, SandboxHostError> {
    let mut components = Vec::new();
    for component in path.split('/') {
        match component {
            "" | "." => {}
            ".." => return invalid_path(path, "contains path traversal"),
            component => components.push(component),
        }
    }
    Ok(components)
}

fn invalid_path<T>(path: &str, reason: &str) -> Result<T, SandboxHostError> {
    Err(SandboxHostError::InvalidRequest(format!(
        "invalid sandbox upload path {path:?}: {reason}"
    )))
}

#[cfg(test)]
#[path = "../tests/sandbox_upload/mod.rs"]
mod tests;
