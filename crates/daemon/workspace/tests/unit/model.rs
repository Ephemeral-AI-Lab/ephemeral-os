use std::collections::{BTreeMap, HashMap};
use std::path::PathBuf;

use crate::network_mode::isolated_network::{
    DnsConfiguration, WorkspaceModeId, WorkspaceRemountState,
};
use crate::overlay::dirs::OverlayDirs;
use crate::overlay::tree::TreeResourceStats;

use super::*;

fn workspace_mode_handle() -> WorkspaceModeHandle {
    WorkspaceModeHandle {
        workspace_id: WorkspaceModeId("isolated-handle".to_owned()),
        network: NetworkMode::IsolatedNetwork,
        caller_id: "caller-1".to_owned(),
        lease_id: "lease-1".to_owned(),
        manifest_version: 42,
        manifest_root_hash: "root-hash".to_owned(),
        workspace_root: "/workspace".to_owned(),
        dirs: OverlayDirs {
            run_dir: "/tmp/eos/run".into(),
            upperdir: "/tmp/eos/upper".into(),
            workdir: "/tmp/eos/work".into(),
        },
        layer_paths: vec!["/lower/one".into(), "/lower/two".into()],
        ns_fds: HashMap::from([("mnt".to_owned(), 11), ("pid".to_owned(), 12)]),
        holder_pid: 1234,
        readiness_fd: 13,
        control_fd: 14,
        veth: None,
        cgroup_path: Some("/sys/fs/cgroup/eos".into()),
        dns_configuration: DnsConfiguration {
            fallback_applied: true,
            previous_first_nameserver: Some("127.0.0.53".to_owned()),
        },
        remount_state: WorkspaceRemountState::Pending,
        created_at: 1.0,
        last_activity: 2.0,
    }
}

fn assert_handle_projection(public: &WorkspaceHandle) {
    assert_eq!(public.id, WorkspaceId("isolated-handle".to_owned()));
    assert_eq!(public.owner, CallerId("caller-1".to_owned()));
    assert_eq!(public.workspace_root, PathBuf::from("/workspace"));
    assert_eq!(public.network, NetworkMode::IsolatedNetwork);
    assert_eq!(
        public.base_revision,
        BaseRevision {
            version: 42,
            root_hash: "root-hash".to_owned(),
            layer_count: 2,
        }
    );
    assert_eq!(
        public.snapshot,
        LayerStackSnapshotRef {
            lease_id: LeaseId("lease-1".to_owned()),
            manifest_version: 42,
            root_hash: "root-hash".to_owned(),
            layer_paths: vec!["/lower/one".into(), "/lower/two".into()],
        }
    );
}

#[test]
fn converts_workspace_mode_handle_to_public_handle() {
    let handle = workspace_mode_handle();

    assert_handle_projection(&WorkspaceHandle::from(&handle));
}

#[test]
fn public_handle_debug_does_not_expose_internal_storage_or_namespace_fields() {
    let public = WorkspaceHandle::from(&workspace_mode_handle());
    let debug = format!("{public:?}");

    assert_no_internal_fields(&debug);
}

#[test]
fn public_dto_debug_does_not_expose_internal_storage_or_namespace_fields() {
    let base_revision = BaseRevision {
        version: 1,
        root_hash: "root".to_owned(),
        layer_count: 1,
    };
    let dtos = [
        format!(
            "{:?}",
            CreateWorkspaceRequest {
                owner: CallerId("caller".to_owned()),
                workspace_root: "/workspace".into(),
                network: NetworkMode::Host,
            }
        ),
        format!(
            "{:?}",
            WorkspaceHandle {
                id: WorkspaceId("workspace".to_owned()),
                owner: CallerId("caller".to_owned()),
                workspace_root: "/workspace".into(),
                network: NetworkMode::Host,
                base_revision: base_revision.clone(),
                snapshot: LayerStackSnapshotRef {
                    lease_id: LeaseId("lease".to_owned()),
                    manifest_version: 1,
                    root_hash: "root".to_owned(),
                    layer_paths: vec!["/lower/one".into()],
                },
            }
        ),
        format!(
            "{:?}",
            RunCommandRequest {
                invocation_id: "invocation".to_owned(),
                cmd: "true".to_owned(),
                cwd: Some("/workspace".into()),
                timeout_seconds: Some(1.0),
                yield_time_ms: 1_000,
                remountable: false,
            }
        ),
        format!(
            "{:?}",
            RunCommandResult {
                status: CommandStatus::Ok,
                command_id: Some("command".to_owned()),
                exit_code: Some(0),
                stdout: String::new(),
                stderr: String::new(),
                changed_paths: Vec::new(),
                base_revision: base_revision.clone(),
                published: false,
            }
        ),
        format!(
            "{:?}",
            CaptureChangesRequest {
                materialize_payloads: false,
                include_stats: true,
            }
        ),
        format!(
            "{:?}",
            CaptureChangesResult {
                workspace_id: WorkspaceId("workspace".to_owned()),
                base_revision,
                changed_paths: Vec::new(),
                changed_path_kinds: BTreeMap::new(),
                protected_drops: Vec::new(),
                stats: None,
            }
        ),
        format!(
            "{:?}",
            DestroyWorkspaceRequest {
                grace_s: Some(1.0),
                cancel_commands: true,
            }
        ),
        format!(
            "{:?}",
            RemountWorkspaceRequest {
                layer_paths: vec!["/lower/one".into()],
            }
        ),
        format!(
            "{:?}",
            RemountWorkspaceResult {
                handle: WorkspaceHandle {
                    id: WorkspaceId("workspace".to_owned()),
                    owner: CallerId("caller".to_owned()),
                    workspace_root: "/workspace".into(),
                    network: NetworkMode::Host,
                    base_revision: BaseRevision {
                        version: 1,
                        root_hash: "root".to_owned(),
                        layer_count: 1,
                    },
                    snapshot: LayerStackSnapshotRef {
                        lease_id: LeaseId("lease".to_owned()),
                        manifest_version: 1,
                        root_hash: "root".to_owned(),
                        layer_paths: vec!["/lower/one".into()],
                    },
                },
            }
        ),
        format!(
            "{:?}",
            LatestSnapshotRequest {
                workspace_root: "/workspace".into(),
                owner_request_id: "request".to_owned(),
            }
        ),
        format!(
            "{:?}",
            ReadonlySnapshotHandle {
                view_root: "/view".into(),
                generation_key: "generation".to_owned(),
                snapshot: LayerStackSnapshotRef {
                    lease_id: LeaseId("lease".to_owned()),
                    manifest_version: 1,
                    root_hash: "root".to_owned(),
                    layer_paths: vec!["/lower/one".into()],
                },
            }
        ),
        format!(
            "{:?}",
            DestroyWorkspaceResult {
                workspace_id: WorkspaceId("workspace".to_owned()),
                owner: CallerId("caller".to_owned()),
                cancelled_commands: 0,
                evicted_upperdir_bytes: 0,
                lifetime_s: 0.0,
                lease_released: Some(true),
                lease_release_error: None,
                active_leases_after: 0,
            }
        ),
    ];

    for debug in dtos {
        assert_no_internal_fields(&debug);
    }
}

fn assert_no_internal_fields(debug: &str) {
    for forbidden in [
        "layer_stack_root:",
        "upperdir:",
        "workdir:",
        "scratch_dir:",
        "ns_fds:",
        "holder_pid:",
        "readiness_fd:",
        "control_fd:",
        "cgroup_path:",
        "veth:",
        "dns_configuration:",
    ] {
        assert!(
            !debug.contains(forbidden),
            "public DTO debug output exposed {forbidden}: {debug}"
        );
    }
}

#[test]
fn public_dtos_construct_clone_and_compare() {
    let base_revision = BaseRevision {
        version: 1,
        root_hash: "root".to_owned(),
        layer_count: 1,
    };
    let create = CreateWorkspaceRequest {
        owner: CallerId("caller".to_owned()),
        workspace_root: "/workspace".into(),
        network: NetworkMode::Host,
    };
    let handle = WorkspaceHandle {
        id: WorkspaceId("workspace".to_owned()),
        owner: CallerId("caller".to_owned()),
        workspace_root: "/workspace".into(),
        network: NetworkMode::Host,
        base_revision: base_revision.clone(),
        snapshot: LayerStackSnapshotRef {
            lease_id: LeaseId("lease".to_owned()),
            manifest_version: 1,
            root_hash: "root".to_owned(),
            layer_paths: vec!["/lower/one".into()],
        },
    };
    let run = RunCommandRequest {
        invocation_id: "invocation".to_owned(),
        cmd: "true".to_owned(),
        cwd: Some("/workspace".into()),
        timeout_seconds: Some(1.5),
        yield_time_ms: 1_000,
        remountable: false,
    };
    let run_result = RunCommandResult {
        status: CommandStatus::Ok,
        command_id: Some("command".to_owned()),
        exit_code: Some(0),
        stdout: String::new(),
        stderr: String::new(),
        changed_paths: vec!["src/main.rs".to_owned()],
        base_revision: base_revision.clone(),
        published: false,
    };
    let capture_request = CaptureChangesRequest {
        materialize_payloads: true,
        include_stats: true,
    };
    let capture = CaptureChangesResult {
        workspace_id: WorkspaceId("workspace".to_owned()),
        base_revision: base_revision.clone(),
        changed_paths: vec!["src/main.rs".to_owned()],
        changed_path_kinds: BTreeMap::from([("src/main.rs".to_owned(), ChangedPathKind::Write)]),
        protected_drops: vec![ProtectedPathDrop {
            path: "fifo".to_owned(),
            reason: ProtectedPathDropReason::UnsupportedSpecialFile,
        }],
        stats: Some(TreeResourceStats {
            files: 1,
            ..TreeResourceStats::default()
        }),
    };
    let destroy_request = DestroyWorkspaceRequest {
        grace_s: Some(1.0),
        cancel_commands: true,
    };
    let remount_request = RemountWorkspaceRequest {
        layer_paths: vec!["/lower/one".into()],
    };
    let remount = RemountWorkspaceResult {
        handle: handle.clone(),
    };
    let latest_request = LatestSnapshotRequest {
        workspace_root: "/workspace".into(),
        owner_request_id: "request".to_owned(),
    };
    let readonly_snapshot = ReadonlySnapshotHandle {
        view_root: "/view".into(),
        generation_key: "generation".to_owned(),
        snapshot: handle.snapshot.clone(),
    };
    let destroy = DestroyWorkspaceResult {
        workspace_id: WorkspaceId("workspace".to_owned()),
        owner: CallerId("caller".to_owned()),
        cancelled_commands: 0,
        evicted_upperdir_bytes: 0,
        lifetime_s: 0.0,
        lease_released: Some(true),
        lease_release_error: None,
        active_leases_after: 0,
    };

    assert_eq!(create.clone(), create);
    assert_eq!(handle.clone(), handle);
    assert_eq!(run.clone(), run);
    assert_eq!(run_result.clone(), run_result);
    assert_eq!(capture_request.clone(), capture_request);
    assert_eq!(capture.clone(), capture);
    assert_eq!(destroy_request.clone(), destroy_request);
    assert_eq!(remount_request.clone(), remount_request);
    assert_eq!(remount.clone(), remount);
    assert_eq!(latest_request.clone(), latest_request);
    assert_eq!(readonly_snapshot.clone(), readonly_snapshot);
    assert_eq!(destroy.clone(), destroy);
    assert_eq!(CommandStatus::TimedOut.as_str(), "timed_out");
}
