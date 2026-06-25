use crate::support::{self, assertion as assert};

// build.rs slug => `lifecycle_create_sandbox_returns_ready`, mounted by tests/manager.rs.
#[test]
fn create_sandbox_returns_ready_with_daemon_endpoint() {
    let Some(h) = support::harness() else {
        return; // skip when not under eos-e2e (EOS_E2E_RUN_ROOT unset)
    };

    // provision_sandbox issues exactly one manager create_sandbox and returns the
    // RAII Sandbox plus the create CallRecord. The id is read from that response
    // /id (runtime-assigned, round-tripped). No second create_sandbox is issued.
    let (_sb, rec) = h.provision_sandbox("lifecycle-create_sandbox-case1", None);

    // _sb is the sole RAII guard; it drops at scope end -> destroy_sandbox.
    // Assert the full M1 contract on the single creation's record.
    let resp = rec.response();
    assert::ok(resp); // no top-level "error"
    let id = assert::field(resp, "/id")
        .as_str()
        .expect("create_sandbox response /id is a string");
    assert!(id
        .chars()
        .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '-' | '_' | '.'))); // charset
    assert_eq!(assert::field(resp, "/state"), "ready"); // SandboxState::Ready
    assert_eq!(assert::field(resp, "/daemon/host"), "127.0.0.1"); // daemon endpoint present
    assert!(assert::field(resp, "/daemon/port").is_u64()); // published TCP port
    // _sb drops here -> manager destroy_sandbox --sandbox-id (the one created).
}
