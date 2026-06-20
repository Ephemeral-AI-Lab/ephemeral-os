#![forbid(unsafe_code)]

#[allow(
    dead_code,
    reason = "test harness path-includes private CLI modules and exercises selected helpers"
)]
#[path = "../src/daemon.rs"]
mod daemon_cli;
#[allow(
    dead_code,
    reason = "test harness path-includes private CLI modules and exercises selected helpers"
)]
#[path = "../src/runner.rs"]
mod runner_cli;

mod daemon_tests {
    include!(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/unit/daemon.rs"));
}

mod runner_tests {
    include!(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/unit/runner.rs"));
}
