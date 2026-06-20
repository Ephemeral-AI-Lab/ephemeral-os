use super::records_value;

pub(crate) fn dispatch(
    services: &crate::operation::ManagerServices,
    request: sandbox_protocol::Request<'_>,
) -> sandbox_protocol::Response {
    match services.store.list() {
        Ok(records) => sandbox_protocol::Response::ok(&request, records_value(records)),
        Err(error) => error.into_response(),
    }
}
