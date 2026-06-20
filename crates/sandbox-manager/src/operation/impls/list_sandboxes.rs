use super::records_value;

pub(crate) fn dispatch(
    services: &crate::operation::ManagerServices,
    request: sandbox_protocol::OperationRequest<'_>,
) -> sandbox_protocol::OperationResponse {
    match services.store.list() {
        Ok(records) => sandbox_protocol::OperationResponse::ok(&request, records_value(records)),
        Err(error) => error.into_response(),
    }
}
