#![forbid(unsafe_code)]

mod host;
mod protocol;
mod runtime;
mod trace_store;

pub use host::{
    ForwardError, ForwardTraceContext, HostConfig, HostForwardRequest, SandboxHost, SandboxStatus,
};
pub use protocol::{strip_trace_sidecar, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES};

#[cfg(feature = "e2e-support")]
pub mod e2e_support {
    pub use crate::protocol::{
        decode_trace_sidecar_base64, encode_request_with_metadata, response_domain_status,
        response_envelope_status, response_fault_kind, response_is_accepted, response_status,
        take_trace_sidecar_checked, ClientError, ProtocolClient, TraceSidecarError,
        TraceWireContext, CONNECT_RETRY_DELAYS_S, DAEMON_AUTH_FIELD, DAEMON_FORWARD_AUTH_FIELD,
        DAEMON_PROTOCOL_FIELD, DAEMON_PROTOCOL_VERSION, DAEMON_TRACE_SIDECAR_ENCODING,
        DAEMON_TRACE_SIDECAR_FIELD, DAEMON_TRACE_SIDECAR_SCHEMA, MAX_REQUEST_BYTES,
        MAX_RESPONSE_BYTES,
    };
    pub use crate::runtime::{
        container_ids_by_ancestor, container_label, copy_path_from_container, docker_available,
        remove_containers_by_label_filters, remove_labeled_containers, running_container_ids,
        ContainerLifetime, ContainerSpec, DaemonContainer, DaemonSpec,
    };
    pub use crate::trace_store::{TraceStore, TraceVerifyFailure, TraceVerifyReport};
}
