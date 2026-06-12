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
        decode_trace_sidecar_base64, error_kind, is_success, response_classification,
        response_status, take_trace_sidecar_checked, ClientError, ProtocolClient,
        ResponseClassification, ResponseShape, TraceSidecarError, DAEMON_TRACE_SIDECAR_FIELD,
    };
    pub use crate::runtime::{
        container_label, docker_available, remove_labeled_containers, running_container_ids,
        ContainerLifetime, ContainerSpec, DaemonContainer, DaemonSpec,
    };
}
