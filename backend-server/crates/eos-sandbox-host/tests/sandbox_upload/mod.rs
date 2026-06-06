#![allow(clippy::unwrap_used)]

use std::io::Read;

use super::*;
use crate::support::MockAdapter;

fn sid() -> SandboxId {
    "sb-upload".parse().unwrap()
}

#[test]
fn destination_rejects_managed_non_eos_paths() {
    for path in ["/tmp", "/proc", "/root", "/var", "/eos/../tmp"] {
        assert!(
            AbsoluteEosPath::parse(path).is_err(),
            "{path} must be rejected"
        );
    }
}

#[test]
fn destination_accepts_and_normalizes_eos_paths() {
    assert_eq!(AbsoluteEosPath::parse("/eos").unwrap().as_str(), "/eos");
    assert_eq!(
        AbsoluteEosPath::parse("/eos//scratch/./uploads")
            .unwrap()
            .as_str(),
        "/eos/scratch/uploads"
    );
}

#[test]
fn tar_entry_paths_reject_traversal_and_absolute_paths() {
    for path in ["", ".", "../escape", "dir/../escape", "/absolute"] {
        assert!(
            SandboxUploadEntry::file(path, b"x".to_vec(), 0o644).is_err(),
            "{path:?} must be rejected"
        );
    }
}

#[test]
fn tar_entry_paths_are_relative_and_normalized() {
    let entry = SandboxUploadEntry::file("runtime//./server.sh", b"x".to_vec(), 0o755).unwrap();
    assert_eq!(entry.path, "runtime/server.sh");
}

#[tokio::test]
async fn upload_file_uses_eos_archive_destination() {
    let adapter = MockAdapter::new();
    let archive_log = adapter.archive_log();

    upload_file_into_eos(
        &adapter,
        &sid(),
        "/eos/scratch/uploads/u1",
        "runtime/server.sh",
        b"#!/bin/sh\n",
        0o755,
    )
    .await
    .unwrap();

    let calls = archive_log.lock().unwrap();
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].dest_dir, "/eos/scratch/uploads/u1");

    let mut archive = tar::Archive::new(calls[0].tar_stream.as_slice());
    let mut entries = archive.entries().unwrap();
    let mut entry = entries.next().unwrap().unwrap();
    assert_eq!(entry.path().unwrap().to_str().unwrap(), "runtime/server.sh");
    assert_eq!(entry.header().mode().unwrap(), 0o755);
    let mut payload = Vec::new();
    entry.read_to_end(&mut payload).unwrap();
    assert_eq!(payload, b"#!/bin/sh\n");
    assert!(entries.next().is_none());
}
