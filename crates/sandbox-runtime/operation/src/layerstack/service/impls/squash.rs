//! The daemon-local manual `squash_layerstack` operation adapter.

use sandbox_operation_catalog::internal::runtime::SQUASH_LAYERSTACK;
use sandbox_operation_contract::OperationScopeKind;
use serde_json::json;

use crate::layerstack::actions::squash;
use crate::operations::dispatch::OperationEntry;
use crate::services::SandboxRuntimeOperations;

const SQUASH_LAYERSTACK_ENTRY: OperationEntry = OperationEntry {
    scope_kind: OperationScopeKind::Sandbox,
    name: SQUASH_LAYERSTACK,
    spec: None,
    dispatch: dispatch_squash_layerstack,
};

const OPERATIONS: &[OperationEntry] = &[SQUASH_LAYERSTACK_ENTRY];

pub(crate) const fn operation_entries() -> &'static [OperationEntry] {
    OPERATIONS
}

fn dispatch_squash_layerstack(
    operations: &SandboxRuntimeOperations,
    _request: &sandbox_operation_contract::OperationRequest,
) -> sandbox_operation_contract::OperationResponse {
    match squash::run_manual(&operations.layerstack, &operations.workspace_session) {
        Ok(result) => sandbox_operation_contract::OperationResponse::ok(result.manual_result),
        Err(message) => sandbox_operation_contract::OperationResponse::fault_with_details(
            "operation_failed",
            message,
            json!({}),
        ),
    }
}
