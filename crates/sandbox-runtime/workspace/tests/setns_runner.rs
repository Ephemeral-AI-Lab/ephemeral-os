#![cfg(target_os = "linux")]

use std::path::PathBuf;

use serde_json::json;

use sandbox_runtime_workspace::overlay::dirs::OverlayDirs;
use sandbox_runtime_workspace::profile::{WorkspaceModeFds, WorkspaceModeHandle, WorkspaceModeId};
use sandbox_runtime_workspace::test_support::{NamespaceRuntime, WorkspaceRemountState};
use sandbox_runtime_workspace::WorkspaceProfile;

#[test]
fn workspace_setns_request_carries_mount_material() {
    let runtime = NamespaceRuntime::new();
    let request = runtime.ns_runner_request(
        &workspace_mode_handle(),
        "remount",
        json!({"probe_path": "probe.txt"}),
        vec!["/lower/next".into()],
    );

    assert_eq!(request.layer_paths, vec![PathBuf::from("/lower/next")]);
    assert_eq!(request.request_id, "isolated-remount-workspace");
}

fn workspace_mode_handle() -> WorkspaceModeHandle {
    WorkspaceModeHandle {
        workspace_id: WorkspaceModeId("workspace".to_owned()),
        profile: WorkspaceProfile::HostCompatible,
        lease_id: "lease-1".to_owned(),
        manifest_version: 1,
        manifest_root_hash: "root-hash".to_owned(),
        base_manifest: sandbox_runtime_layerstack::Manifest::new(
            1,
            vec![sandbox_runtime_layerstack::LayerRef {
                layer_id: "L000001-test".to_owned(),
                path: "layers/L000001-test".to_owned(),
            }],
            sandbox_runtime_layerstack::MANIFEST_SCHEMA_VERSION,
        )
        .expect("test manifest is valid"),
        workspace_root: "/workspace".to_owned(),
        dirs: OverlayDirs {
            run_dir: "/tmp/eos/run".into(),
            upperdir: "/tmp/eos/upper".into(),
            workdir: "/tmp/eos/work".into(),
        },
        layer_paths: vec!["/lower/base".into()],
        ns_fds: WorkspaceModeFds {
            user: Some(10),
            mnt: Some(11),
            pid: Some(12),
            net: None,
        },
        holder_pid: 1234,
        readiness_fd: 13,
        control_fd: 14,
        veth: None,
        remount_state: WorkspaceRemountState::Active,
        created_at: 1.0,
        last_activity: 2.0,
    }
}
