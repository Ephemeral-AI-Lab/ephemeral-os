use super::records_value;

pub(crate) fn dispatch(
    services: &crate::operation::ManagerServices,
    _request: &sandbox_protocol::Request,
) -> sandbox_protocol::Response {
    match services.store.list() {
        Ok(records) => sandbox_protocol::Response::ok(records_value(records)),
        Err(error) => error.into_response(),
    }
}
