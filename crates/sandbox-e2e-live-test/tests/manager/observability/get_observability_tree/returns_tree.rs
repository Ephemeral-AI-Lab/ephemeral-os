use crate::support::{self, assertion as assert};

// build.rs slug => `observability_get_observability_tree_returns_tree`, mounted by tests/manager.rs.
#[test]
fn observability_tree_lists_sandbox_and_clamps_over_limit() {
    let Some(h) = support::harness() else {
        return; // skip when not under eos-e2e (EOS_E2E_RUN_ROOT unset)
    };

    let (sb, _create) = h.provision_sandbox("observability-get_observability_tree-case1", None);

    // Scoped to our sandbox, with bounded recent traces at the documented max.
    let rec = h.cli().manager(
        "get_observability_tree",
        &[
            "--sandbox-id",
            &sb.id,
            "--include-recent-traces",
            "1",
            "--trace-limit",
            "100",
        ],
    );
    sb.record(&rec);
    let resp = rec.response();
    assert::ok(resp); // no top-level "error"

    assert_eq!(assert::field(resp, "/sandboxes/0/sandbox_id"), sb.id.as_str());
    let availability = assert::field(resp, "/sandboxes/0/availability")
        .as_str()
        .expect("availability is a string");
    assert!(
        matches!(availability, "available" | "partial" | "unavailable"),
        "unexpected availability {availability:?} in {resp}"
    );
    // Every observability node carries these keys (defaulted when empty).
    for key in ["resources", "workspaces", "recent_traces", "errors"] {
        let _ = assert::field(resp, &format!("/sandboxes/0/{key}"));
    }

    // An over-limit trace_limit is clamped (MAX_TRACE_LIMIT), never rejected.
    let over_limit = h.cli().manager(
        "get_observability_tree",
        &[
            "--sandbox-id",
            &sb.id,
            "--include-recent-traces",
            "1",
            "--trace-limit",
            "9999",
        ],
    );
    sb.record(&over_limit);
    assert::ok(over_limit.response()); // clamped, still ok
    // sb drops here -> flush exchange.jsonl, then destroy_sandbox --sandbox-id sb.id.
}
