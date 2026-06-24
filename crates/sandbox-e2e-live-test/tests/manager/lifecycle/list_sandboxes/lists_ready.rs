use crate::support::{self, assertion as assert};

// build.rs slug => `lifecycle_list_sandboxes_lists_ready`, mounted by tests/manager.rs.
#[test]
fn list_sandboxes_contains_ready_sandbox() {
    let Some(h) = support::harness() else {
        return; // skip when not under eos-e2e (EOS_E2E_RUN_ROOT unset)
    };

    // One provisioned sandbox; the create record seeds this sandbox's exchange.jsonl.
    let (sb, _create) = h.provision_sandbox("lifecycle-list_sandboxes-case1", None);

    // list_sandboxes takes no args; the live store returns every record.
    let rec = h.cli().manager("list_sandboxes", &[]);
    sb.record(&rec);
    let resp = rec.response();
    assert::ok(resp); // no top-level "error"

    let sandboxes = assert::field(resp, "/sandboxes")
        .as_array()
        .expect("/sandboxes is an array");
    // Some element is the sandbox we just provisioned, reported ready.
    assert!(
        sandboxes.iter().any(|entry| {
            entry.pointer("/id").and_then(|v| v.as_str()) == Some(sb.id.as_str())
                && entry.pointer("/state").and_then(|v| v.as_str()) == Some("ready")
        }),
        "no ready sandbox with id {} in {resp}",
        sb.id
    );
    // sb drops here -> flush exchange.jsonl, then destroy_sandbox --sandbox-id sb.id.
}
