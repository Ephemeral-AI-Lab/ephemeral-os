//! Shared sandbox RPC protocol primitives.
//!
//! This crate defines generic request and response types plus protocol-neutral
//! operation metadata. It does not open sockets, dispatch operations, or know
//! command/workspace semantics.

#![forbid(unsafe_code)]

pub mod auth;
pub mod catalog;
pub mod cli_operation_spec;
pub mod error_kind;
mod framing;
pub mod help;
pub mod limits;
pub mod request;
pub mod response;
pub mod scope;

pub use auth::{DAEMON_AUTH_FIELD, GATEWAY_AUTH_FIELD};
pub use catalog::{
    catalog_from_value, catalog_to_value, operation_execution_space_name, ArgCliSpecDocument,
    ArgSpecDocument, CatalogDecodeError, CliOperationCatalog, CliOperationCatalogDocument,
    CliOperationExecutionSpace, CliOperationFamilyDocument, CliOperationSpecDocument,
    CliSpecDocument,
};
pub use cli_operation_spec::{
    ArgCliSpec, ArgKind, ArgSpec, CliOperationFamilySpec, CliOperationSpec, CliSpec,
};
pub use help::{
    render_catalog_help, render_operation_help, search_operation_help, CliOperationSearchResult,
    HelpRenderError,
};
pub use limits::ProtocolLimits;
pub use request::{decode_request_value, Request};
pub use response::{error_response_with_details, response_line, Response};
pub use scope::CliOperationScope;
