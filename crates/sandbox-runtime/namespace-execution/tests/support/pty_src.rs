pub mod transcript {
    include!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/transcript.rs"));
}

pub mod pty {
    include!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/pty.rs"));

    pub fn terminate_pgid_for_test() -> fn(i32) {
        terminate_pgid
    }
}
