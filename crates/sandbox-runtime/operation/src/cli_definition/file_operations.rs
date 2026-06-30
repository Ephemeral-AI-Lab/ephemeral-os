use serde_json::{json, Value};

use crate::cli_definition::{
    ArgCliSpec, ArgKind, ArgSpec, CliOperationFamilySpec, CliOperationSpec, CliSpec,
};
use crate::file::{BlameRange, FileError};
use crate::operation::OperationEntry;
use crate::SandboxRuntimeOperations;
use sandbox_protocol::{Request, Response};

const FILE_NOT_FOUND: &str = "not_found";

pub(crate) const FILE_FAMILY: CliOperationFamilySpec = CliOperationFamilySpec {
    id: "file",
    title: "File",
    summary: "Inspect published file line ownership.",
    description: "Query per-line ownership over the publish auditability log. Only blame ships now; read/write/edit join the same service later.",
};

const FILE_BLAME_SPEC: CliOperationSpec = CliOperationSpec {
    name: "file_blame",
    family: "file",
    summary: "Show per-line ownership for a published file.",
    description: "Return each line's owner for a published path, tiling the whole file from the latest auditability event. The owner is an opaque string (workspace_session:<id> | operation:<id> | original | unknown).",
    args: FILE_BLAME_ARGS,
    cli: Some(CliSpec {
        path: &["runtime", "file_blame"],
        usage: "sandbox-cli runtime file_blame --path FILE",
        examples: &["sandbox-cli runtime file_blame --path README.md"],
    }),
    related: &[],
};

const FILE_BLAME_ARGS: &[ArgSpec] = &[ArgSpec::required(
    "path",
    ArgKind::String,
    "Repository-relative path to blame.",
    Some(ArgCliSpec {
        flag: Some("--path"),
        positional: None,
    }),
)];

const FILE_BLAME: OperationEntry = OperationEntry::cli(&FILE_BLAME_SPEC, dispatch_file_blame);

const OPERATIONS: &[OperationEntry] = &[FILE_BLAME];

pub(crate) const fn operation_entries() -> &'static [OperationEntry] {
    OPERATIONS
}

fn dispatch_file_blame(operations: &SandboxRuntimeOperations, request: &Request) -> Response {
    let path = match request.required_string("path") {
        Ok(path) => path,
        Err(response) => return response,
    };
    match operations.file.blame(&path) {
        Ok(ranges) => Response::ok(file_blame_value(&path, &ranges)),
        Err(FileError::NotFound(missing)) => Response::fault_with_details(
            FILE_NOT_FOUND,
            format!("no auditability record for path: {missing}"),
            json!({ "path": missing }),
        ),
    }
}

fn file_blame_value(path: &str, ranges: &[BlameRange]) -> Value {
    json!({
        "path": path,
        "ranges": ranges
            .iter()
            .map(|range| {
                json!({
                    "start_line": range.start_line,
                    "line_count": range.line_count,
                    "owner": range.owner,
                })
            })
            .collect::<Vec<_>>(),
    })
}
