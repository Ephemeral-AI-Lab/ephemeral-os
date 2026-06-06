#![allow(clippy::unwrap_used)]
use std::io::Read;
use std::sync::Arc;

use super::*;
use crate::provider::RawExecResult;
use crate::registry::ProviderRegistry;
use crate::support::MockAdapter;

fn sid() -> eos_types::SandboxId {
    "sb-1".parse().unwrap()
}

// AC-08 lockstep is a compile-time assert above; assert the value here too.
#[test]
fn protocol_version_lockstep() {
    assert_eq!(
        PROTOCOL_VERSION,
        crate::daemon_client::DAEMON_PROTOCOL_VERSION
    );
    assert_eq!(PROTOCOL_VERSION, 1);
}

#[test]
fn artifact_arch_maps_and_rejects() {
    assert_eq!(artifact_arch("x86_64").unwrap(), "amd64");
    assert_eq!(artifact_arch(" AMD64 ").unwrap(), "amd64");
    assert_eq!(artifact_arch("aarch64").unwrap(), "arm64");
    assert_eq!(artifact_arch("arm64").unwrap(), "arm64");
    let err = artifact_arch("riscv64").unwrap_err();
    assert!(matches!(
        err,
        SandboxHostError::UnsupportedArchitecture { machine } if machine == "riscv64"
    ));
}

#[test]
fn pinned_shas_are_64_hex() {
    for (arch, sha) in EOSD_SHA256 {
        assert_eq!(sha.len(), 64, "{arch} sha length");
        assert!(sha.bytes().all(|b| b.is_ascii_hexdigit()), "{arch} sha hex");
    }
    assert!(expected_sha("amd64").is_some());
    assert!(expected_sha("riscv64").is_none());
}

#[test]
fn tar_stream_is_deterministic_single_file() {
    let payload = b"#!/bin/true\n";
    let stream = crate::sandbox_upload::tar_file_at_path("eosd", payload, 0o755).unwrap();
    let mut archive = tar::Archive::new(&stream[..]);
    let mut entries = archive.entries().unwrap();
    let mut entry = entries.next().unwrap().unwrap();
    assert_eq!(entry.path().unwrap().to_str().unwrap(), "eosd");
    assert_eq!(entry.header().mode().unwrap(), 0o755);
    assert_eq!(entry.header().mtime().unwrap(), 0);
    let mut content = Vec::new();
    entry.read_to_end(&mut content).unwrap();
    assert_eq!(content, payload);
    assert!(entries.next().is_none(), "exactly one entry");
}

#[test]
fn marker_skip_decision() {
    let digest = "abc123";
    let hit = RawExecResult {
        exit_code: 0,
        stdout: "abc123\n".to_owned(),
        stderr: String::new(),
        success: true,
    };
    assert!(marker_indicates_skip(&hit, digest));
    let wrong = RawExecResult {
        stdout: "deadbeef".to_owned(),
        ..hit.clone()
    };
    assert!(!marker_indicates_skip(&wrong, digest));
    let absent = RawExecResult {
        exit_code: 1,
        stdout: "abc123".to_owned(),
        stderr: String::new(),
        success: false,
    };
    assert!(!marker_indicates_skip(&absent, digest));
}

// AC-06: a host artifact whose digest differs from the pin returns
// ArtifactHashMismatch; a missing artifact returns ArtifactMissing.
#[tokio::test]
async fn upload_verifies_hash_and_missing() {
    let tmp = std::env::temp_dir().join(format!("eosd-test-{}", uuid::Uuid::new_v4().simple()));
    tokio::fs::create_dir_all(&tmp).await.unwrap();
    // amd64 arch (mock `uname -m` → x86_64), but a fake binary != pin.
    tokio::fs::write(tmp.join("eosd-linux-amd64"), b"not the real binary")
        .await
        .unwrap();
    let adapter = MockAdapter::new().with_exec(|cmd| {
        let stdout = if cmd.contains("uname -m") {
            "x86_64"
        } else {
            ""
        };
        RawExecResult {
            exit_code: 0,
            stdout: stdout.to_owned(),
            stderr: String::new(),
            success: true,
        }
    });
    let registry = ProviderRegistry::new();
    let adapter_arc: Arc<dyn ProviderAdapter> = Arc::new(adapter);
    registry.set_default(Arc::clone(&adapter_arc));

    let err = ensure_daemon_bootstrap(&*adapter_arc, &sid(), &tmp)
        .await
        .unwrap_err();
    assert!(matches!(
        err,
        SandboxHostError::ArtifactHashMismatch { arch, .. } if arch == "amd64"
    ));

    // Remove the artifact → ArtifactMissing.
    tokio::fs::remove_file(tmp.join("eosd-linux-amd64"))
        .await
        .unwrap();
    let err = ensure_daemon_bootstrap(&*adapter_arc, &sid(), &tmp)
        .await
        .unwrap_err();
    assert!(matches!(err, SandboxHostError::ArtifactMissing { arch } if arch == "amd64"));

    tokio::fs::remove_dir_all(&tmp).await.ok();
}
