use sandbox_cli::core::request_builder::{catalog_document, RequestBuildError};
use sandbox_protocol::CliOperationCatalogDocument;

use crate::config::OperationSet;

/// Load the one public-operation catalog selected for this MCP process.
///
/// # Errors
/// Returns an error when the selected source catalog is invalid.
pub fn selected_catalog(
    set: OperationSet,
) -> Result<CliOperationCatalogDocument, RequestBuildError> {
    catalog_document(match set {
        OperationSet::Management => sandbox_manager_operations::manager_catalog(),
        OperationSet::Runtime => sandbox_runtime_operations::runtime_catalog(),
        OperationSet::Observability => sandbox_observability_operations::observability_catalog(),
    })
}
