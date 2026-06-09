use super::*;

#[test]
fn cancellation_handle_keeps_first_reason() {
    let (handle, signal) = agent_loop_cancel_pair();

    handle.cancel("first");
    handle.cancel("second");

    assert_eq!(signal.reason().as_deref(), Some("first"));
}
