use super::dispatch::ManagerOperationEntry;

mod management;

pub(crate) fn operation_entries() -> &'static [ManagerOperationEntry] {
    management::operation_entries()
}
