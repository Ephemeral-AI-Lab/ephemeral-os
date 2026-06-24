use crate::support::{self, assertion as assert};

// build.rs slug => `command_exec_command_one_shot`, mounted by tests/runtime.rs.
#[test]
fn one_shot_exec_returns_ok_and_zero_exit() {
    let Some(h) = support::harness() else {
        return; // skip when not under eos-e2e (EOS_E2E_RUN_ROOT unset)
    };
    let (sb, _create) = h.provision_sandbox("command-exec_command-case1", None); // one create_sandbox; id from /id

    // sandbox-cli runtime --sandbox-id {sb.id} exec_command pwd
    let rec = h.cli().runtime(&sb.id, "exec_command", &["pwd"]);
    let resp = rec.response();
    assert::ok(resp); // success: no top-level "error"
    assert_eq!(assert::field(resp, "/status"), "ok"); // CommandStatus::Ok => "ok"
    assert_eq!(assert::field(resp, "/exit_code"), 0); // terminal one-shot, zero exit
    assert!(resp.get("command_session_id").is_none()); // terminal => field absent
    // sb drops here -> manager destroy_sandbox --sandbox-id sb.id
}
