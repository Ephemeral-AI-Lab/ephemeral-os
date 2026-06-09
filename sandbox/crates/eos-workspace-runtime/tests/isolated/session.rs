use std::cell::RefCell;
use std::collections::HashMap;
use std::path::PathBuf;
use std::rc::Rc;
use std::time::{SystemTime, UNIX_EPOCH};

use eos_workspace_runtime::isolated::audit::AuditSink;
use eos_workspace_runtime::isolated::{
    CallerId, IsolatedError, IsolatedSession, LayerStackSnapshotPort, NamespaceRuntimePort,
    ResourceCaps, SnapshotLease, WorkspaceHandle,
};
use serde_json::Value;

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
    let released = Rc::new(RefCell::new(Vec::new()));
    let events = Rc::new(RefCell::new(Vec::new()));
    let layer_stack = RecordingLayerStack {
        released: Rc::clone(&released),
    };
    let runtime = NoopRuntime;
    let audit = RecordingAudit {
        events: Rc::clone(&events),
    };
    let mut session =
        IsolatedSession::with_scratch_root(caps, layer_stack, runtime, audit, scratch_root.clone());
    let caller = CallerId("caller-1".to_owned());

    let handle = session.enter(&caller)?;
    let upperdir = handle.upperdir.clone();
    std::fs::write(upperdir.join("private.txt"), b"private bytes")?;

    let exit = session.exit(&caller, Some(0.0))?;

    assert!(!upperdir.exists());
    assert_eq!(released.borrow().as_slice(), ["lease-1".to_owned()]);
    assert!(session.list_open_callers().is_empty());
    assert_eq!(exit["evicted_upperdir_bytes"], serde_json::json!(13));
    let recorded_events = events.borrow();
    assert!(recorded_events
        .iter()
        .any(|(kind, _)| kind == "sandbox_isolated_workspace_enter"));
    assert!(recorded_events.iter().any(|(kind, payload)| {
        kind == "sandbox_isolated_workspace_exit"
            && payload["upperdir_bytes_discarded"] == serde_json::json!(13)
            && payload["scratch_removed"] == serde_json::json!(true)
    }));

    let _ = std::fs::remove_dir_all(scratch_root);
    Ok(())
}

struct RecordingLayerStack {
    released: Rc<RefCell<Vec<String>>>,
}

impl LayerStackSnapshotPort for RecordingLayerStack {
    fn acquire_snapshot(&self, _request_id: &str) -> Result<SnapshotLease, IsolatedError> {
        Ok(SnapshotLease {
            lease_id: "lease-1".to_owned(),
            manifest_version: 7,
            manifest_root_hash: "root-hash".to_owned(),
            layer_paths: vec![PathBuf::from("/lower")],
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
        _layer_paths: &[PathBuf],
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

struct RecordingAudit {
    events: Rc<RefCell<Vec<(String, Value)>>>,
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
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |duration| duration.as_nanos());
    std::env::temp_dir().join(format!("eos-{prefix}-{}-{nanos}", std::process::id()))
}
