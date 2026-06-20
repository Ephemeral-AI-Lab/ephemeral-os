pub(crate) fn dispatch(
    _services: &crate::operation::ManagerServices,
    request: sandbox_protocol::Request<'_>,
) -> sandbox_protocol::SandboxResponse {
    sandbox_protocol::SandboxResponse::ok(
        &request,
        crate::operation::specs::catalog_value(crate::operation::operation_catalog()),
    )
}
