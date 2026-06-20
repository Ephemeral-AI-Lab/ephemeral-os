#![forbid(unsafe_code)]
#![allow(dead_code)]

#[path = "../src/container.rs"]
mod container;
#[path = "../src/daemon_wire.rs"]
mod daemon_wire;
#[path = "../src/service/mod.rs"]
mod service;

pub(crate) use container::{
    container_copy_target, daemon_spawn_args, docker_display, docker_exec_args, docker_run_args,
    parse_published_addr, redact_docker_error_text, validate_remote_name, ContainerLifetime,
    ContainerSpec,
};
pub(crate) use service::workspace_root_from_args;

const _: usize = std::mem::size_of::<service::ForwardError>();

pub(crate) mod runtime_tests {
    include!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/unit/runtime.rs"
    ));
}

mod daemon_wire_tests {
    include!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/unit/daemon_wire.rs"
    ));
}

mod host_tests {
    include!(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/unit/host.rs"));
}
