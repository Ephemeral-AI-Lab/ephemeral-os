use sandbox_cli::core::request_builder::{catalog_document, RequestBuildError};
use sandbox_operation_contract::OperationCatalogDocument;

use crate::config::OperationSet;

/// Load the one public-operation catalog selected for this MCP process.
///
/// # Errors
/// Returns an error when the selected source catalog is invalid.
pub fn selected_catalog(set: OperationSet) -> Result<OperationCatalogDocument, RequestBuildError> {
    catalog_document(match set {
        OperationSet::Management => sandbox_operation_catalog::manager::manager_catalog(),
        OperationSet::Runtime => sandbox_operation_catalog::runtime::runtime_catalog(),
        OperationSet::Observability => {
            sandbox_operation_catalog::observability::observability_catalog()
        }
    })
}
