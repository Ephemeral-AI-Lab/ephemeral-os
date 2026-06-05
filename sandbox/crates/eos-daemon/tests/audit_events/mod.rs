use std::sync::atomic::{AtomicU64, Ordering};

use serde_json::{json, Value};

use super::*;

type TestResult<T = ()> = Result<T, Box<dyn std::error::Error + Send + Sync>>;

#[test]
fn dispatch_audit_emits_typed_tool_call_and_resource_sample() -> TestResult {
    let suffix = unique_suffix();
    let invocation_id = format!("dispatch-{suffix}");
    let tool_use_id = format!("toolu-{suffix}");
    let request = Request {
        op: "api.v1.read_file".to_owned(),
        invocation_id: invocation_id.clone(),
        args: json!({"invocation_id": &tool_use_id, "agent_id": "agent-1"}),
    };
    let response = json!({
        "status": "ok",
        "workspace_mode": "ephemeral",
        "timings": {
            "custom.phase": 0.125,
            "resource.cgroup.cpu_user_usec": 2_500_000.0,
            "resource.cgroup.cpu_system_usec": 500_000.0,
            "resource.cgroup.cpu_throttled_usec": 7.0,
            "resource.cgroup.io_rbytes": 1024.0,
            "resource.cgroup.io_wbytes": 2048.0,
            "resource.cgroup.io_rios": 3.0,
            "resource.cgroup.io_wios": 4.0
        }
    });
    let after_seq = audit_after_seq()?;

    emit_dispatch_audit(&request, &response, 0.010);

    let events = events_after(after_seq)?;
    let tool_event = events
        .iter()
        .find(|event| {
            event["type"].as_str() == Some("tool_call.completed")
                && event["payload"]["tool_call"]["tool_use_id"].as_str()
                    == Some(tool_use_id.as_str())
        })
        .ok_or("tool_call.completed event")?;
    assert_eq!(tool_event["lane"], json!("normal"));
    assert_eq!(
        tool_event["payload"]["tool_call"]["tool_name"],
        json!("api.v1.read_file")
    );
    assert_eq!(
        tool_event["payload"]["tool_call"]["phase_totals_rollup"]["custom.phase"],
        json!(0.125)
    );
    assert!(tool_event["payload"]["tool_call"]
        .get("workspace_handle_id")
        .is_none());

    let resource_event = events
        .iter()
        .find(|event| {
            event["type"].as_str() == Some(OS_RESOURCE_SAMPLED)
                && event["payload"]["os_resource"]["tool_use_id"].as_str()
                    == Some(tool_use_id.as_str())
        })
        .ok_or("os_resource.sampled event")?;
    assert_eq!(resource_event["lane"], json!("sample"));
    assert_eq!(
        resource_event["payload"]["os_resource"]["operation_id"],
        json!(invocation_id)
    );
    assert_eq!(
        resource_event["payload"]["os_resource"]["agent_id"],
        json!("agent-1")
    );
    assert_eq!(
        resource_event["payload"]["os_resource"]["cpu_user_s"],
        json!(2.5)
    );
    assert_eq!(
        resource_event["payload"]["os_resource"]["cpu_system_s"],
        json!(0.5)
    );
    assert_eq!(
        resource_event["payload"]["os_resource"]["cpu_throttled_us"],
        json!(7)
    );
    assert_eq!(
        resource_event["payload"]["os_resource"]["io_read_bytes"],
        json!(1024)
    );
    assert!(
        resource_event["payload"]["os_resource"]["sampled_at_monotonic_s"]
            .as_f64()
            .unwrap_or(-1.0)
            >= 0.0
    );
    Ok(())
}

#[test]
fn dispatch_audit_emits_workspace_base_events() -> TestResult {
    let suffix = unique_suffix();
    let request = Request {
        op: "api.ensure_workspace_base".to_owned(),
        invocation_id: format!("workspace-base-{suffix}"),
        args: json!({"layer_stack_root": "/missing/eos-e2e-root"}),
    };
    let response = json!({
        "success": true,
        "binding": {
            "active_manifest_version": 1
        },
        "timings": {
            "api.workspace_base.total_s": 0.012
        }
    });
    let after_seq = audit_after_seq()?;

    emit_dispatch_audit(&request, &response, 0.001);

    let events = events_after(after_seq)?;
    let event = events
        .iter()
        .find(|event| {
            event["type"].as_str() == Some("workspace_base.ensured")
                && event["payload"]["layer_stack"]["operation_id"].as_str()
                    == Some(request.invocation_id.as_str())
        })
        .ok_or("workspace_base.ensured event")?;
    assert_eq!(event["lane"], json!("normal"));
    assert_eq!(
        event["payload"]["layer_stack"]["manifest_version"],
        json!(1)
    );
    assert_eq!(event["payload"]["layer_stack"]["total_ms"], json!(12.0));
    Ok(())
}

#[test]
fn dispatch_audit_emits_commit_completed() -> TestResult {
    let suffix = unique_suffix();
    let request = Request {
        op: "api.commit_to_workspace".to_owned(),
        invocation_id: format!("commit-{suffix}"),
        args: json!({"layer_stack_root": "/missing/eos-e2e-root"}),
    };
    let response = json!({
        "success": true,
        "manifest_version": 3,
        "timings": {
            "api.commit_to_workspace.total_s": 0.034
        }
    });
    let after_seq = audit_after_seq()?;

    emit_dispatch_audit(&request, &response, 0.001);

    let events = events_after(after_seq)?;
    let event = events
        .iter()
        .find(|event| {
            event["type"].as_str() == Some("layer_stack.commit_completed")
                && event["payload"]["layer_stack"]["operation_id"].as_str()
                    == Some(request.invocation_id.as_str())
        })
        .ok_or("layer_stack.commit_completed event")?;
    assert_eq!(event["lane"], json!("normal"));
    assert_eq!(
        event["payload"]["layer_stack"]["manifest_version"],
        json!(3)
    );
    assert_eq!(event["payload"]["layer_stack"]["total_ms"], json!(34.0));
    Ok(())
}

fn audit_after_seq() -> TestResult<i64> {
    let snapshot = crate::audit::buffer::global_audit_buffer().snapshot();
    Ok(snapshot["snapshot"]["daemon"]["next_seq"]
        .as_i64()
        .unwrap_or(0)
        - 1)
}

fn events_after(after_seq: i64) -> TestResult<Vec<Value>> {
    let pulled = crate::audit::buffer::global_audit_buffer().pull(after_seq, 256);
    Ok(pulled["events"].as_array().ok_or("events array")?.clone())
}

fn unique_suffix() -> String {
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    format!(
        "{}-{}",
        std::process::id(),
        COUNTER.fetch_add(1, Ordering::Relaxed)
    )
}
