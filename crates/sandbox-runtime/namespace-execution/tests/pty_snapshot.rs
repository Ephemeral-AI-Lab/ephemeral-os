//! A read-only worker snapshot must not initialize the process-global PTY reactor.

include!("support/namespace_execution_src.rs");

#[test]
fn empty_snapshot_does_not_start_pty_reactor() {
    let first = pty::output_reactor_snapshot();
    let second = pty::output_reactor_snapshot();

    assert_eq!(first.worker_threads, 0);
    assert_eq!(first.active_readers, 0);
    assert_eq!(second.worker_threads, 0);
    assert_eq!(second.active_readers, 0);
}
