use crate::support::{self, assertion as assert};

// build.rs slug => `lifecycle_destroy_sandbox_removes_sandbox`, mounted by tests/manager.rs.
#[test]
fn destroy_sandbox_removes_then_inspect_errors() {
    let Some(h) = support::harness() else {
        return; // skip when not under eos-e2e (EOS_E2E_RUN_ROOT unset)
    };

    let (sb, _create) = h.provision_sandbox("lifecycle-destroy_sandbox-case1", None);

    // destroy_sandbox returns the removed record_value (so /id round-trips).
    let destroy = h.cli().manager("destroy_sandbox", &["--sandbox-id", &sb.id]);
    sb.record(&destroy);
    let resp = destroy.response();
    assert::ok(resp); // no top-level "error"
    assert_eq!(assert::field(resp, "/id"), sb.id.as_str());

    // The id is now removed; inspecting it is MissingSandbox => invalid_request,
    // rendered to stderr on exit 1.
    let inspect = h.cli().manager("inspect_sandbox", &["--sandbox-id", &sb.id]);
    sb.record(&inspect);
    assert::err_kind_at(&inspect, "invalid_request", 1);
    // sb drops here -> flush exchange.jsonl, then a best-effort second
    // destroy_sandbox on the now-removed id (MissingSandbox, swallowed).
}
