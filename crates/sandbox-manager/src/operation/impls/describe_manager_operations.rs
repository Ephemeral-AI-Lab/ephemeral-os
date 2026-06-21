pub(crate) fn dispatch(
    _services: &crate::operation::ManagerServices,
    _request: &sandbox_protocol::Request,
) -> sandbox_protocol::Response {
    sandbox_protocol::Response::ok(sandbox_protocol::catalog_to_value(
        crate::operation::operation_catalog(),
    ))
}
