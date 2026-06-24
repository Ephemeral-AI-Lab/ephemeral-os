pub mod pty {
    include!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/pty.rs"));

    const _TERMINATE_PROCESS_GROUP_REF: fn(i32) = terminate_process_group;
}
