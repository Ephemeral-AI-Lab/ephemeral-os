use super::{first_nameserver, needs_fallback_dns, overlay_layer_paths, require_ns_fds};
use crate::protocol::{Fd, NamespaceCommandRequest, NsFds, WorkspaceRoot};
use std::path::Path;
#[cfg(target_os = "linux")]
use std::path::PathBuf;

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
fn remount_overlay_requires_setns_payload() -> Result<(), Box<dyn std::error::Error>> {
    let mut request = request(None);
    request.layer_paths = vec![Path::new("/tmp/layer").to_path_buf()];

    let Err(error) = super::remount_overlay(&request, &runner_config()) else {
        return Err("remount overlay should require setns namespace fds".into());
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

#[cfg(target_os = "linux")]
#[test]
fn lowerdir_verification_reports_only_available_kernel_proof() {
    let expected = vec![PathBuf::from("/layers/l4"), PathBuf::from("/layers/parent")];
    assert_eq!(
        super::mountinfo_lowerdir_count_matched(None, expected.len()),
        None
    );
    assert_eq!(super::mountinfo_lowerdir_verified(None, &expected), None);

    let hidden = super::WorkspaceMountInfo {
        mount_point: "/workspace".to_owned(),
        fs_type: "overlay".to_owned(),
        lowerdir_count: None,
        lowerdir: None,
    };
    assert_eq!(
        super::mountinfo_lowerdir_count_matched(Some(&hidden), expected.len()),
        None
    );
    assert_eq!(
        super::mountinfo_lowerdir_verified(Some(&hidden), &expected),
        None
    );

    let count_only = super::WorkspaceMountInfo {
        lowerdir_count: Some(2),
        ..hidden.clone()
    };
    assert_eq!(
        super::mountinfo_lowerdir_count_matched(Some(&count_only), expected.len()),
        Some(true)
    );
    assert_eq!(
        super::mountinfo_lowerdir_verified(Some(&count_only), &expected),
        None
    );

    let exact = super::WorkspaceMountInfo {
        lowerdir_count: Some(2),
        lowerdir: Some("/layers/l4:/layers/parent".to_owned()),
        ..hidden.clone()
    };
    assert_eq!(
        super::mountinfo_lowerdir_verified(Some(&exact), &expected),
        Some(true)
    );

    let mismatch = super::WorkspaceMountInfo {
        lowerdir: Some("/layers/parent:/layers/l4".to_owned()),
        ..exact
    };
    assert_eq!(
        super::mountinfo_lowerdir_verified(Some(&mismatch), &expected),
        Some(false)
    );
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

fn request(ns_fds: Option<NsFds>) -> NamespaceCommandRequest {
    NamespaceCommandRequest {
        invocation_id: "test".to_owned(),
        caller_id: "caller".to_owned(),
        args: serde_json::json!({"command": "true"}),
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

#[cfg(target_os = "linux")]
fn runner_config() -> crate::runner::config::RunnerConfig {
    crate::runner::config::RunnerConfig {
        child_wait_poll_ms: 5,
        mount_mask: crate::runner::config::RunnerMountMaskConfig {
            hidden_paths: vec![Path::new("/eos").to_path_buf()],
        },
        env: crate::runner::config::RunnerEnvConfig {
            inherit_keys: vec![],
            restricted_keys: vec![],
            default_path: "/usr/bin:/bin".to_owned(),
            testbed_path_prefix: vec![],
        },
    }
}
