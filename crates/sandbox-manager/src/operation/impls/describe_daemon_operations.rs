use super::{endpoint, sandbox_id};

pub(crate) fn dispatch(
    services: &crate::operation::ManagerServices,
    request: &sandbox_protocol::Request,
) -> sandbox_protocol::Response {
    let id = match sandbox_id(request) {
        Ok(id) => id,
        Err(response) => return response,
    };
    let endpoint = match endpoint(services, &id) {
        Ok(endpoint) => endpoint,
        Err(error) => return error.into_response(),
    };
    match services.daemon_client.describe_operations(&endpoint) {
        Ok(catalog) => sandbox_protocol::Response::ok(sandbox_protocol::catalog_to_value(catalog)),
        Err(error) => error.into_response(),
    }
}
