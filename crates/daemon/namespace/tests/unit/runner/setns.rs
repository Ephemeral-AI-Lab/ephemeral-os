use super::{first_nameserver, needs_fallback_dns, overlay_layer_paths, require_ns_fds};
use crate::protocol::{Fd, NsFds, RunMode, RunRequest, RunnerVerb, ToolCall, WorkspaceRoot};
use std::path::Path;

#[test]
fn require_ns_fds_rejects_missing_setns_payload() -> Result<(), Box<dyn std::error::Error>> {
    let Err(error) = require_ns_fds(&request(None)) else {
        return Err("ns_fds should be required".into());
    };
    assert!(error.to_string().contains("requires ns_fds"));
    Ok(())
}

#[cfg(target_os = "linux")]
#[test]
fn namespace_order_is_user_mnt_pid_net_and_skips_missing_fds() {
    let ns_fds = NsFds {
        user: Some(Fd(10)),
        mnt: Some(Fd(11)),
        pid: None,
        net: Some(Fd(12)),
    };
    let order: Vec<(&str, i32)> = super::namespace_fd_order_with_types(&ns_fds)
        .into_iter()
        .map(|(name, fd, _nstype)| (name, fd))
        .collect();
    assert_eq!(order, vec![("user", 10), ("mnt", 11), ("net", 12)]);
}

#[test]
fn overlay_layer_paths_fall_back_to_workspace_root() {
    let request = request(Some(default_ns_fds()));
    assert_eq!(
        overlay_layer_paths(&request),
        vec![Path::new("/workspace").to_path_buf()]
    );
}

#[test]
fn dns_fallback_applies_only_to_loopback_first_nameserver() {
    let content = "search local\nnameserver 127.0.0.53\nnameserver 8.8.8.8\n";
    let nameserver = first_nameserver(content);
    assert_eq!(nameserver, Some("127.0.0.53"));
    assert!(needs_fallback_dns(nameserver.unwrap_or_default()));
    assert!(!needs_fallback_dns("10.244.0.1"));
    assert_eq!(first_nameserver("search local\n"), None);
}

fn request(ns_fds: Option<NsFds>) -> RunRequest {
    RunRequest {
        mode: RunMode::SetNs,
        tool_call: ToolCall {
            invocation_id: "test".to_owned(),
            caller_id: "caller".to_owned(),
            verb: RunnerVerb::ExecCommand,
            args: serde_json::json!({"command": "true"}),
            background: false,
        },
        workspace_root: WorkspaceRoot(Path::new("/workspace").to_path_buf()),
        layer_paths: vec![],
        upperdir: Some(Path::new("/tmp/iws/upper").to_path_buf()),
        workdir: Some(Path::new("/tmp/iws/work").to_path_buf()),
        ns_fds,
        cgroup_path: None,
        timeout_seconds: None,
    }
}

fn default_ns_fds() -> NsFds {
    NsFds {
        user: Some(Fd(10)),
        mnt: Some(Fd(11)),
        pid: Some(Fd(12)),
        net: Some(Fd(13)),
    }
}
