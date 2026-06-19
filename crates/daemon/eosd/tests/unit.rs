#![forbid(unsafe_code)]
#![allow(dead_code)]

#[path = "../src/daemon.rs"]
mod daemon_cli;
#[path = "../src/runner.rs"]
mod runner_cli;

mod daemon_tests {
    include!(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/unit/daemon.rs"));
}

mod runner_tests {
    include!(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/unit/runner.rs"));
}
