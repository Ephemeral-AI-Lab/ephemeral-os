use super::{HostGatewayErrorKind, ProtocolErrorKind};

#[test]
fn protocol_error_kind_wire_names_are_stable() {
    assert_eq!(ProtocolErrorKind::ServerBusy.as_str(), "server_busy");
    assert_eq!(
        ProtocolErrorKind::RequestTooLarge.as_str(),
        "request_too_large"
    );
}

#[test]
fn host_gateway_error_kind_wire_names_are_stable() {
    assert_eq!(
        HostGatewayErrorKind::TraceUnavailable.as_str(),
        "trace_unavailable"
    );
    assert_eq!(
        HostGatewayErrorKind::UncertainOutcome.as_str(),
        "uncertain_outcome"
    );
}
