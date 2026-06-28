use super::ManagerServices;

#[derive(Clone, Copy)]
pub(crate) struct ManagerOperationEntry {
    pub(crate) spec: &'static sandbox_protocol::CliOperationSpec,
    pub(crate) dispatch:
        fn(&ManagerServices, &sandbox_protocol::Request) -> sandbox_protocol::Response,
}

impl ManagerOperationEntry {
    #[must_use]
    pub(crate) const fn new(
        spec: &'static sandbox_protocol::CliOperationSpec,
        dispatch: fn(&ManagerServices, &sandbox_protocol::Request) -> sandbox_protocol::Response,
    ) -> Self {
        Self { spec, dispatch }
    }
}

#[must_use]
pub fn dispatch_operation(
    services: &ManagerServices,
    request: &sandbox_protocol::Request,
) -> sandbox_protocol::Response {
    super::cli_definition::operation_entries()
        .iter()
        .find(|entry| entry.spec.name == request.op)
        .map_or_else(sandbox_protocol::Response::unknown_op, |entry| {
            (entry.dispatch)(services, request)
        })
}
