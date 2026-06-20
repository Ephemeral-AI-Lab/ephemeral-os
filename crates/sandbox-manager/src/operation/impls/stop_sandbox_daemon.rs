use super::{record_value, sandbox_id};

pub(crate) fn dispatch(
    services: &crate::operation::ManagerServices,
    request: sandbox_protocol::Request<'_>,
) -> sandbox_protocol::Response {
    let id = match sandbox_id(&request) {
        Ok(id) => id,
        Err(response) => return response,
    };
    let record = match services.store.inspect(&id) {
        Ok(record) => record,
        Err(error) => return error.into_response(),
    };
    if record.daemon.is_none() {
        return crate::ManagerError::DaemonUnavailable { id }.into_response();
    }
    if let Err(error) = services.daemon_installer.stop_daemon(&record) {
        return error.into_response();
    }
    match services.store.update_endpoint(&record.id, None) {
        Ok(record) => sandbox_protocol::Response::ok(&request, record_value(record)),
        Err(error) => error.into_response(),
    }
}
