use std::cell::RefCell;
use std::collections::HashMap;
use std::path::PathBuf;

use serde_json::Value;

use super::capacity::{
    check_host_capacity_against_budget, host_capacity_budget_bytes_from_memavailable_kib,
    parse_memavailable_kib, required_host_capacity_bytes,
};
use super::support::next_handle_id;
use super::{
    AgentId, IsolatedError, IsolatedSession, LayerStackSnapshotPort, NamespaceRuntimePort,
    SnapshotLease, WorkspaceHandle,
};
use crate::audit::AuditSink;
use crate::caps::ResourceCaps;

#[test]
fn parses_memavailable_from_proc_meminfo() {
    let meminfo = "MemTotal:       1024 kB\nMemAvailable:    2048 kB\n";
    assert_eq!(parse_memavailable_kib(meminfo), Some(2_048));
}

#[test]
fn host_capacity_budget_matches_python_floor() {
    assert_eq!(
        host_capacity_budget_bytes_from_memavailable_kib(1_001, 0.5),
        512_512
    );
}

#[test]
fn host_capacity_required_saturates() {
    assert_eq!(required_host_capacity_bytes(usize::MAX, u64::MAX), u64::MAX);
}

#[test]
fn host_capacity_rejects_when_required_exceeds_budget() -> Result<(), Box<dyn std::error::Error>> {
    let error = match check_host_capacity_against_budget(2, 10, 29) {
        Ok(()) => return Err("expected host RAM pressure rejection".into()),
        Err(error) => error,
    };
    let (required_bytes, budget_bytes) = match error {
        IsolatedError::HostRamPressure {
            required_bytes,
            budget_bytes,
        } => (required_bytes, budget_bytes),
        other => return Err(format!("expected host RAM pressure error, got {other}").into()),
    };
    assert_eq!(required_bytes, 30);
    assert_eq!(budget_bytes, 29);
    Ok(())
}

#[test]
fn next_handle_id_puts_counter_in_veth_name_prefix() {
    let first = next_handle_id();
    let second = next_handle_id();

    assert_eq!(first.len(), 22);
    assert_eq!(second.len(), 22);
    assert_ne!(&first[..6], &second[..6]);
}

#[test]
fn isolated_exit_discards_upperdir_and_exposes_no_publish_path(
) -> Result<(), Box<dyn std::error::Error>> {
    let scratch_root = unique_temp_dir("isolated-no-publish");
    let caps = ResourceCaps {
        enabled: true,
        total_cap: 2,
        eos_workspace_root: "/workspace".to_owned(),
        ..ResourceCaps::default()
    };
    let layer_stack = RecordingLayerStack::default();
    let runtime = NoopRuntime;
    let audit = RecordingAudit::default();
    let mut session =
        IsolatedSession::with_scratch_root(caps, layer_stack, runtime, audit, scratch_root.clone());
    let agent = AgentId("agent-1".to_owned());

    let handle = session.enter(&agent)?;
    let upperdir = handle.upperdir.clone();
    std::fs::write(upperdir.join("private.txt"), b"private bytes")?;

    let exit = session.exit(&agent, Some(0.0))?;

    assert!(!upperdir.exists());
    assert_eq!(
        session.layer_stack.released.borrow().as_slice(),
        ["lease-1".to_owned()]
    );
    assert!(session.by_agent.is_empty());
    assert!(session.handles.is_empty());
    assert_eq!(exit["evicted_upperdir_bytes"], serde_json::json!(13));
    let events = session.audit.events.borrow();
    assert!(events
        .iter()
        .any(|(kind, _)| kind == "sandbox_isolated_workspace_enter"));
    assert!(events.iter().any(|(kind, payload)| {
        kind == "sandbox_isolated_workspace_exit"
            && payload["upperdir_bytes_discarded"] == serde_json::json!(13)
            && payload["scratch_removed"] == serde_json::json!(true)
    }));

    let _ = std::fs::remove_dir_all(scratch_root);
    Ok(())
}

#[derive(Default)]
struct RecordingLayerStack {
    released: RefCell<Vec<String>>,
}

impl LayerStackSnapshotPort for RecordingLayerStack {
    fn acquire_snapshot(&self, _request_id: &str) -> Result<SnapshotLease, IsolatedError> {
        Ok(SnapshotLease {
            lease_id: "lease-1".to_owned(),
            manifest_version: 7,
            root_hash: "root-hash".to_owned(),
            layer_paths: vec!["/lower".to_owned()],
        })
    }

    fn release_lease(&self, lease_id: &str) -> Result<bool, IsolatedError> {
        self.released.borrow_mut().push(lease_id.to_owned());
        Ok(true)
    }

    fn active_lease_count(&self) -> Result<Option<usize>, IsolatedError> {
        Ok(Some(0))
    }
}

struct NoopRuntime;

impl NamespaceRuntimePort for NoopRuntime {
    fn spawn_ns_holder(
        &self,
        _handle: &mut WorkspaceHandle,
        _setup_timeout_s: f64,
    ) -> Result<i32, IsolatedError> {
        Ok(0)
    }

    fn open_ns_fds(&self, _holder_pid: i32) -> Result<HashMap<String, i32>, IsolatedError> {
        Ok(HashMap::new())
    }

    fn mount_overlay(
        &self,
        _handle: &WorkspaceHandle,
        _layer_paths: &[String],
    ) -> Result<(), IsolatedError> {
        Ok(())
    }

    fn configure_dns(
        &self,
        _handle: &WorkspaceHandle,
        _fallback_dns: &str,
    ) -> Result<bool, IsolatedError> {
        Ok(false)
    }

    fn signal_net_ready(
        &self,
        _handle: &WorkspaceHandle,
        _setup_timeout_s: f64,
    ) -> Result<(), IsolatedError> {
        Ok(())
    }

    fn create_cgroup(&self, _handle: &WorkspaceHandle) -> Result<PathBuf, IsolatedError> {
        Ok(PathBuf::new())
    }

    fn kill_holder(&self, _holder_pid: i32, _grace_s: f64) -> Result<(), IsolatedError> {
        Ok(())
    }
}

#[derive(Default)]
struct RecordingAudit {
    events: RefCell<Vec<(String, Value)>>,
}

impl AuditSink for RecordingAudit {
    fn emit(&self, event_type: &str, payload: Value) -> Result<(), IsolatedError> {
        self.events
            .borrow_mut()
            .push((event_type.to_owned(), payload));
        Ok(())
    }
}

fn unique_temp_dir(prefix: &str) -> PathBuf {
    std::env::temp_dir().join(format!(
        "eos-{prefix}-{}-{}",
        std::process::id(),
        next_handle_id()
    ))
}
