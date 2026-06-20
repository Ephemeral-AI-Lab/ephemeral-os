use sandbox_protocol::{decode_request_object, ArgsPresence};

use super::{endpoint, request_object, sandbox_id};

pub(crate) fn dispatch(
    services: &crate::operation::ManagerServices,
    request: sandbox_protocol::Request<'_>,
) -> sandbox_protocol::Response {
    let id = match sandbox_id(&request) {
        Ok(id) => id,
        Err(response) => return response,
    };
    let endpoint = match endpoint(services, &id) {
        Ok(endpoint) => endpoint,
        Err(error) => return error.into_response(),
    };
    let nested = match request_object(&request, "request").and_then(|object| {
        decode_request_object(object, ArgsPresence::Required).map_err(|err| {
            request.invalid_argument(format!("request is invalid: {}", err.message()))
        })
    }) {
        Ok(request) => request,
        Err(response) => return response,
    };
    match services.daemon_client.invoke(&endpoint, nested) {
        Ok(response) => response,
        Err(error) => error.into_response(),
    }
}
