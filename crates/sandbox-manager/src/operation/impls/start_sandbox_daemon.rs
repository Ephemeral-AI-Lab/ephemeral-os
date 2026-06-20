use super::{ready_record, record_value, sandbox_id};

pub(crate) fn dispatch(
    services: &crate::operation::ManagerServices,
    request: sandbox_protocol::OperationRequest<'_>,
) -> sandbox_protocol::OperationResponse {
    let id = match sandbox_id(&request) {
        Ok(id) => id,
        Err(response) => return response,
    };
    let record = match ready_record(services, &id) {
        Ok(record) => record,
        Err(error) => return error.into_response(),
    };
    if let Err(error) = services.daemon_installer.install_daemon(&record) {
        return error.into_response();
    }
    let endpoint = match services.daemon_installer.start_daemon(&record) {
        Ok(endpoint) => endpoint,
        Err(error) => return error.into_response(),
    };
    if let Err(error) = services.daemon_installer.check_daemon(&endpoint) {
        return error.into_response();
    }
    match services.store.update_endpoint(&id, Some(endpoint)) {
        Ok(record) => sandbox_protocol::OperationResponse::ok(&request, record_value(record)),
        Err(error) => error.into_response(),
    }
}
