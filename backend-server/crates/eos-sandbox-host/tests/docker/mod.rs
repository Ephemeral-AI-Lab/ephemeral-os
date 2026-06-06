#![allow(clippy::unwrap_used)]
use super::*;
use bollard::models::{ContainerConfig, ContainerStateStatusEnum};

#[test]
fn serialize_container_normalizes_shape() {
    let mut labels = HashMap::new();
    labels.insert("managed_by".to_owned(), "eos".to_owned());
    labels.insert("snapshot".to_owned(), "py:3.11".to_owned());
    labels.insert("project_dir".to_owned(), "/workspace".to_owned());
    let inspect = ContainerInspectResponse {
        id: Some("abc123".to_owned()),
        name: Some("/my-box".to_owned()),
        config: Some(ContainerConfig {
            image: Some("python:3.11".to_owned()),
            labels: Some(labels),
            working_dir: Some("/ignored".to_owned()),
            ..Default::default()
        }),
        state: Some(ContainerState {
            status: Some(ContainerStateStatusEnum::RUNNING),
            ..Default::default()
        }),
        ..Default::default()
    };
    let info = serialize_container(&inspect).unwrap();
    assert_eq!(info.id.as_str(), "abc123");
    assert_eq!(info.name, "my-box"); // leading '/' stripped
    assert_eq!(info.image.as_deref(), Some("python:3.11"));
    assert_eq!(info.snapshot.as_deref(), Some("py:3.11"));
    assert_eq!(info.state, "running"); // lowercased
    assert_eq!(info.project_dir.as_deref(), Some("/workspace")); // label wins over working_dir
    assert!(info.managed_by_app);
}

#[test]
fn serialize_container_unmanaged_falls_back_to_working_dir() {
    let inspect = ContainerInspectResponse {
        id: Some("x".to_owned()),
        name: Some("plain".to_owned()),
        config: Some(ContainerConfig {
            working_dir: Some("/srv".to_owned()),
            ..Default::default()
        }),
        ..Default::default()
    };
    let info = serialize_container(&inspect).unwrap();
    assert_eq!(info.project_dir.as_deref(), Some("/srv"));
    assert!(!info.managed_by_app);
    assert_eq!(info.state, ""); // no state → empty
}

#[test]
fn normalize_string_map_drops_empty_keys_and_trims() {
    let mut input = Labels::new();
    input.insert(" key ".to_owned(), " value ".to_owned());
    input.insert("   ".to_owned(), "dropped".to_owned());
    let out = normalize_string_map(&input);
    assert_eq!(out.get("key").map(String::as_str), Some("value"));
    assert_eq!(out.len(), 1);
}

#[test]
fn container_env_splits_first_equals() {
    let inspect = ContainerInspectResponse {
        config: Some(ContainerConfig {
            env: Some(vec![
                "EOS_DAEMON_AUTH_TOKEN=tok=en".to_owned(),
                "NO_EQUALS".to_owned(),
            ]),
            ..Default::default()
        }),
        ..Default::default()
    };
    let env = container_env(&inspect);
    assert_eq!(
        env.get("EOS_DAEMON_AUTH_TOKEN").map(String::as_str),
        Some("tok=en")
    );
    assert!(!env.contains_key("NO_EQUALS"));
}

// AC-07: the eosd upload uses an UNCOMPRESSED tar via `put_archive` — the
// Docker fast path (no base64-chunk fallback exists in this crate). The live
// `upload_to_container` call is exercised only under the `docker` feature
// against a real daemon; here we assert the fast-path payload contract that
// `DockerProviderAdapter::put_archive` forwards verbatim.
#[test]
fn put_archive_fast_path() {
    let stream = crate::sandbox_upload::tar_file_at_path("eosd", b"binary", 0o755).unwrap();
    assert_ne!(
        &stream[..2],
        &[0x1f, 0x8b],
        "fast path is a plain tar, never gzip"
    );
    let mut archive = tar::Archive::new(&stream[..]);
    assert_eq!(
        archive.entries().unwrap().count(),
        1,
        "single-file fast-path tar stream"
    );
}

#[test]
fn eos_tmpfs_upload_destinations_use_exec_tar_route() {
    assert!(is_eos_tmpfs_destination("/eos"));
    assert!(is_eos_tmpfs_destination("/eos/runtime/daemon"));
    assert!(is_eos_tmpfs_destination("/eos/scratch/uploads/u1"));
    assert!(!is_eos_tmpfs_destination("/eos-other"));
    assert!(!is_eos_tmpfs_destination("/tmp"));
}

#[test]
fn image_not_found_detection() {
    let err = bollard::errors::Error::DockerResponseServerError {
        status_code: 404,
        message: "No such image: ghost:latest".to_owned(),
    };
    assert!(is_image_not_found(&err));
    let other = bollard::errors::Error::DockerResponseServerError {
        status_code: 500,
        message: "boom".to_owned(),
    };
    assert!(!is_image_not_found(&other));
}
