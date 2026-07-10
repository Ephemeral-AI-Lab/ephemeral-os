#![forbid(unsafe_code)]

#[allow(
    dead_code,
    reason = "test harness path-includes private CLI modules and exercises selected helpers"
)]
#[path = "../src/cgroup_setup.rs"]
pub(crate) mod cgroup_setup;
#[allow(
    dead_code,
    reason = "rpc lifecycle references crate::http; the harness includes it to resolve that path"
)]
#[path = "../src/http/mod.rs"]
pub(crate) mod http;
#[path = "../src/observability/mod.rs"]
pub(crate) mod observability;
#[allow(
    dead_code,
    unused_imports,
    reason = "test harness path-includes rpc modules and exercises selected private helpers"
)]
#[path = "../src/rpc/mod.rs"]
pub(crate) mod rpc;
#[allow(
    dead_code,
    reason = "test harness path-includes private CLI modules and exercises selected helpers"
)]
#[path = "../src/runner/mod.rs"]
mod runner_cli;
#[allow(
    dead_code,
    reason = "test harness path-includes private CLI modules and exercises selected helpers"
)]
#[path = "../src/serve.rs"]
mod serve_cli;

#[path = "unit/dependency_guard.rs"]
mod dependency_guard_tests;

mod connection_tests {
    pub(crate) use crate::rpc::connection::read_request_line_with_limits;
    pub(crate) use crate::rpc::lifecycle::drain_connection_tasks;
    include!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/unit/connection.rs"
    ));
}

mod dispatch_tests {
    pub(crate) use crate::rpc::dispatch::{
        daemon_readiness_response, decode_request, strip_tcp_auth, validate_daemon_scope,
    };
    pub(crate) use crate::rpc::SandboxDaemonError;
    include!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/unit/dispatch.rs"
    ));
}

mod observability_tests {
    include!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/unit/observability.rs"
    ));
}

mod observability_layerstack_tests {
    include!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/unit/observability_layerstack.rs"
    ));
}

mod http_tests {
    include!(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/unit/http.rs"));
}

mod cgroup_setup_tests {
    include!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/unit/cgroup_setup.rs"
    ));
}

mod runner_tests {
    include!(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/unit/runner.rs"));
}

mod serve_tests {
    include!(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/unit/serve.rs"));
}
