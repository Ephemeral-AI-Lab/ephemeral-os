use super::{endpoint, sandbox_id};

pub(crate) fn dispatch(
    services: &crate::operation::ManagerServices,
    request: sandbox_protocol::OperationRequest<'_>,
) -> sandbox_protocol::OperationResponse {
    let id = match sandbox_id(&request) {
        Ok(id) => id,
        Err(response) => return response,
    };
    let endpoint = match endpoint(services, &id) {
        Ok(endpoint) => endpoint,
        Err(error) => return error.into_response(),
    };
    match services.daemon_client.describe_operations(&endpoint) {
        Ok(catalog) => sandbox_protocol::OperationResponse::ok(
            &request,
            crate::operation::specs::catalog_value(catalog),
        ),
        Err(error) => error.into_response(),
    }
}
