use super::{record_value, sandbox_id};

pub(crate) fn dispatch(
    services: &crate::operation::ManagerServices,
    request: sandbox_protocol::Request<'_>,
) -> sandbox_protocol::Response {
    let id = match sandbox_id(&request) {
        Ok(id) => id,
        Err(response) => return response,
    };
    match services.store.inspect(&id) {
        Ok(record) => sandbox_protocol::Response::ok(&request, record_value(record)),
        Err(error) => error.into_response(),
    }
}
