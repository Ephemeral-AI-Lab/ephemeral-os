#![forbid(unsafe_code)]

mod host;
pub mod protocol;
mod runtime;
pub mod trace_store;

pub use host::{
    ForwardError, ForwardTraceContext, ForwardTraceEvent, HostConfig, SandboxHost, SandboxStatus,
};
pub use protocol::MAX_REQUEST_BYTES;

pub mod e2e_support {
    pub use crate::protocol::{
        decode_trace_sidecar_base64, response_fault_kind, response_is_accepted, response_status,
        take_trace_sidecar_checked, ClientError, ProtocolClient, TraceSidecarError,
        DAEMON_TRACE_SIDECAR_ENCODING, DAEMON_TRACE_SIDECAR_FIELD, DAEMON_TRACE_SIDECAR_SCHEMA,
    };
    pub use crate::runtime::{
        container_label, docker_available, remove_labeled_containers, running_container_ids,
        ContainerLifetime, ContainerSpec, DaemonContainer, DaemonSpec,
    };
}
