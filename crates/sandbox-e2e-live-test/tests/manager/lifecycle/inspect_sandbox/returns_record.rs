use crate::support::{self, assertion as assert};

// build.rs slug => `lifecycle_inspect_sandbox_returns_record`, mounted by tests/manager.rs.
#[test]
fn inspect_sandbox_returns_full_record() {
    let Some(h) = support::harness() else {
        return; // skip when not under eos-e2e (EOS_E2E_RUN_ROOT unset)
    };

    let (sb, _create) = h.provision_sandbox("lifecycle-inspect_sandbox-case1", None);

    // inspect_sandbox requires --sandbox-id; success returns the full record_value.
    let rec = h.cli().manager("inspect_sandbox", &["--sandbox-id", &sb.id]);
    sb.record(&rec);
    let resp = rec.response();
    assert::ok(resp); // no top-level "error"

    assert_eq!(assert::field(resp, "/id"), sb.id.as_str()); // round-tripped id
    // record_value carries the full {id,workspace_root,state,daemon} record.
    let _ = assert::field(resp, "/workspace_root");
    let _ = assert::field(resp, "/state");
    let _ = assert::field(resp, "/daemon");
    // sb drops here -> flush exchange.jsonl, then destroy_sandbox --sandbox-id sb.id.
}
