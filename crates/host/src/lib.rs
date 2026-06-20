#![forbid(unsafe_code)]

mod container;
mod daemon_wire;
mod service;

pub use daemon_wire::{MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES};
pub use service::{ForwardError, HostConfig, HostForwardRequest, SandboxHost, SandboxStatus};

#[cfg(feature = "e2e-support")]
pub mod e2e_support {
    pub use crate::container::{
        container_ids_by_ancestor, container_label, copy_path_from_container, docker_available,
        remove_containers_by_label_filters, remove_labeled_containers, running_container_ids,
        ContainerLifetime, ContainerSpec, DaemonContainer, DaemonSpec,
    };
    pub use crate::daemon_wire::{
        encode_request_with_metadata, response_domain_status, response_envelope_status,
        response_fault_kind, response_is_accepted, response_status, ClientError, ProtocolClient,
        CONNECT_RETRY_DELAYS_S, DAEMON_AUTH_FIELD, DAEMON_FORWARD_AUTH_FIELD, MAX_REQUEST_BYTES,
        MAX_RESPONSE_BYTES,
    };
}
