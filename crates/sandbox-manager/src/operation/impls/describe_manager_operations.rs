pub(crate) fn dispatch(
    _services: &crate::operation::ManagerServices,
    request: sandbox_protocol::OperationRequest<'_>,
) -> sandbox_protocol::OperationResponse {
    sandbox_protocol::OperationResponse::ok(
        &request,
        crate::operation::specs::catalog_value(crate::operation::operation_catalog()),
    )
}
