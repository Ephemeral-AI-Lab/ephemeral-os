#![forbid(unsafe_code)]

pub mod argument;
pub mod document;
pub mod domain;
pub mod error;
pub mod family;
pub mod operation;
pub mod request;
pub mod response;
pub mod route;
pub mod scope;

pub use argument::{ArgKind, ArgSpec};
pub use document::{
    catalog_from_value, catalog_to_value, ArgSpecDocument, CatalogDecodeError, OperationCatalog,
    OperationCatalogDocument, OperationFamilyDocument, OperationRouteDocument,
    OperationSpecDocument,
};
pub use domain::{operation_domain_name, OperationDomain};
pub use error::{error_response_with_details, OperationError};
pub use family::OperationFamilySpec;
pub use operation::OperationSpec;
pub use request::OperationRequest;
pub use response::OperationResponse;
pub use route::{
    OperationExecutionOwner, OperationRouteSpec, OperationScopePolicy, OperationVisibility,
};
pub use scope::{OperationScope, OperationScopeKind};
