//! [`PluginToolSpec`] — the catalog-native, model-facing tool-spec sources.
//!
//! Ports the **declared** surface of `plugins/catalog/lsp/tools/*.py`: one input
//! struct, one name const, one `const DESCRIPTION`, and one [`Intent`] per tool
//! (anchor §10). It moves no `call_plugin` dispatch and no Pyright runtime
//! (GC-plugin-catalog-05) — `eos-agent-core` binds each [`PluginToolSpec`] into a
//! real `eos_llm_client::ToolSpec` + a `ToolExecutor`, which is why this crate
//! has no `eos-tool`/`eos-llm-client` edge (GC-plugin-catalog-04).
//!
//! Deliberate type choices that change the emitted schema vs Pydantic (so the
//! AC-10 parity comparison is *normalized*, not byte-equal): `line`/`character`
//! are `u32` (Python `Field(ge=0)` → the non-negative bound is structural), and
//! opaque LSP payloads (`range`, `diagnostics[]`, `action`, `edit`, `options`)
//! stay [`JsonObject`] (the daemon-side Pyright runtime owns the full
//! `WorkspaceEdit`/`CodeAction` schema, GC-plugin-catalog-05).

use eos_sandbox_port::Intent;
use eos_types::JsonObject;
use schemars::schema::RootSchema;
use schemars::{schema_for, JsonSchema};
use serde::Deserialize;

use super::names::PluginToolName;

/// A catalog-native, model-facing tool-spec **source** (not an
/// `eos_llm_client::ToolSpec`). `eos-agent-core` binds this into a real `ToolSpec`
/// + `ToolExecutor` that routes through `SandboxTransport`.
#[derive(Debug, Clone)]
#[non_exhaustive]
pub struct PluginToolSpec {
    /// The fully-qualified tool name (e.g. `lsp.hover`).
    pub name: PluginToolName,
    /// The model-facing description (the colocated `const DESCRIPTION`).
    pub description: &'static str,
    /// The input JSON schema generated from the tool's input struct.
    pub input_schema: RootSchema,
    /// The sandbox execution intent the bound executor must honor.
    pub intent: Intent,
}

/// All built-in plugin tool specs (today: the 10 LSP specs), built eagerly. No
/// deferred/lazy model-facing tool loading (anchor §2).
#[must_use]
pub fn plugin_tool_specs() -> Vec<PluginToolSpec> {
    vec![
        spec(
            HOVER,
            HOVER_DESCRIPTION,
            schema_for!(HoverInput),
            Intent::ReadOnly,
        ),
        spec(
            FIND_DEFINITIONS,
            FIND_DEFINITIONS_DESCRIPTION,
            schema_for!(FindDefinitionsInput),
            Intent::ReadOnly,
        ),
        spec(
            FIND_REFERENCES,
            FIND_REFERENCES_DESCRIPTION,
            schema_for!(FindReferencesInput),
            Intent::ReadOnly,
        ),
        spec(
            DIAGNOSTICS,
            DIAGNOSTICS_DESCRIPTION,
            schema_for!(DiagnosticsInput),
            Intent::ReadOnly,
        ),
        spec(
            QUERY_SYMBOLS,
            QUERY_SYMBOLS_DESCRIPTION,
            schema_for!(QuerySymbolsInput),
            Intent::ReadOnly,
        ),
        spec(
            RENAME,
            RENAME_DESCRIPTION,
            schema_for!(RenameInput),
            Intent::WriteAllowed,
        ),
        spec(
            FORMAT,
            FORMAT_DESCRIPTION,
            schema_for!(FormatInput),
            Intent::WriteAllowed,
        ),
        spec(
            CODE_ACTIONS,
            CODE_ACTIONS_DESCRIPTION,
            schema_for!(CodeActionsInput),
            Intent::ReadOnly,
        ),
        spec(
            APPLY_CODE_ACTION,
            APPLY_CODE_ACTION_DESCRIPTION,
            schema_for!(ApplyCodeActionInput),
            Intent::WriteAllowed,
        ),
        spec(
            APPLY_WORKSPACE_EDIT,
            APPLY_WORKSPACE_EDIT_DESCRIPTION,
            schema_for!(ApplyWorkspaceEditInput),
            Intent::WriteAllowed,
        ),
    ]
}

fn spec(
    name: &'static str,
    description: &'static str,
    input_schema: RootSchema,
    intent: Intent,
) -> PluginToolSpec {
    PluginToolSpec {
        name: PluginToolName::new(name),
        description,
        input_schema,
        intent,
    }
}

// Tool names — the manifest declares `lsp.format`, not `format_document`; the
// source wins (GC-plugin-catalog-07).
const HOVER: &str = "lsp.hover";
const FIND_DEFINITIONS: &str = "lsp.find_definitions";
const FIND_REFERENCES: &str = "lsp.find_references";
const DIAGNOSTICS: &str = "lsp.diagnostics";
const QUERY_SYMBOLS: &str = "lsp.query_symbols";
const RENAME: &str = "lsp.rename";
const FORMAT: &str = "lsp.format";
const CODE_ACTIONS: &str = "lsp.code_actions";
const APPLY_CODE_ACTION: &str = "lsp.apply_code_action";
const APPLY_WORKSPACE_EDIT: &str = "lsp.apply_workspace_edit";

// Model-facing descriptions — the exact `description=` text from each `@tool`.
const HOVER_DESCRIPTION: &str =
    "Return Pyright hover information for a Python symbol at the given cursor.";
const FIND_DEFINITIONS_DESCRIPTION: &str =
    "Return definition locations for the Python symbol at the given cursor.";
const FIND_REFERENCES_DESCRIPTION: &str =
    "Return references to the Python symbol at the given cursor.";
const DIAGNOSTICS_DESCRIPTION: &str =
    "Return Pyright diagnostics (errors, warnings, hints) for a Python file.";
const QUERY_SYMBOLS_DESCRIPTION: &str =
    "Return workspace or per-file Python symbol matches for the given query fragment.";
const RENAME_DESCRIPTION: &str =
    "Rename a Python symbol with Pyright and publish the workspace edit.";
const FORMAT_DESCRIPTION: &str = "Format a Python file through Pyright and publish the edit.";
const CODE_ACTIONS_DESCRIPTION: &str = "Return Pyright code actions for a Python file range.";
const APPLY_CODE_ACTION_DESCRIPTION: &str = "Apply a Pyright CodeAction edit and publish it.";
const APPLY_WORKSPACE_EDIT_DESCRIPTION: &str =
    "Apply an LSP WorkspaceEdit to the workspace and publish it.";

fn default_true() -> bool {
    true
}

/// The default `lsp.format` options (`{"tabSize": 4, "insertSpaces": true}`).
fn default_format_options() -> JsonObject {
    let mut options = JsonObject::new();
    options.insert("tabSize".to_owned(), serde_json::Value::from(4));
    options.insert("insertSpaces".to_owned(), serde_json::Value::Bool(true));
    options
}

// The input structs are crate-private schema sources: the runtime binds the
// generated `input_schema`, never the Rust type. Field doc comments double as the
// schemars property descriptions; `#[allow(dead_code)]` because schemars reads the
// field *types*, so the values are never read here.
#[allow(dead_code)]
#[derive(Debug, Deserialize, JsonSchema)]
struct HoverInput {
    /// Repo-relative or absolute file path.
    file_path: String,
    /// 0-based line number.
    line: u32,
    /// 0-based character offset on the line.
    character: u32,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize, JsonSchema)]
struct FindDefinitionsInput {
    /// Repo-relative or absolute file path.
    file_path: String,
    /// 0-based line number.
    line: u32,
    /// 0-based character offset on the line.
    character: u32,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize, JsonSchema)]
struct FindReferencesInput {
    /// Repo-relative or absolute file path.
    file_path: String,
    /// 0-based line number.
    line: u32,
    /// 0-based character offset on the line.
    character: u32,
    /// Include the symbol's own declaration.
    #[serde(default = "default_true")]
    include_declaration: bool,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize, JsonSchema)]
struct DiagnosticsInput {
    /// Repo-relative or absolute file path.
    file_path: String,
    /// Wait for at least one Pyright diagnostic before returning.
    #[serde(default)]
    wait_for_diagnostics: bool,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize, JsonSchema)]
struct QuerySymbolsInput {
    /// Symbol name fragment.
    query: String,
    /// Optional file path to restrict the search to one document.
    #[serde(default)]
    file_path: Option<String>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize, JsonSchema)]
struct RenameInput {
    /// Repo-relative or absolute file path.
    file_path: String,
    /// 0-based line number.
    line: u32,
    /// 0-based character offset.
    character: u32,
    /// Replacement symbol name (non-empty; enforced at the daemon boundary).
    new_name: String,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize, JsonSchema)]
struct FormatInput {
    /// Repo-relative or absolute file path.
    file_path: String,
    /// LSP formatting options.
    #[serde(default = "default_format_options")]
    options: JsonObject,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize, JsonSchema)]
struct CodeActionsInput {
    /// Repo-relative or absolute file path.
    file_path: String,
    /// 0-based line number.
    #[serde(default)]
    line: u32,
    /// 0-based character offset.
    #[serde(default)]
    character: u32,
    /// Optional LSP range.
    #[serde(default)]
    range: Option<JsonObject>,
    /// Optional diagnostics to scope the code actions.
    #[serde(default)]
    diagnostics: Vec<JsonObject>,
    /// Optional code-action kinds to filter by.
    #[serde(default)]
    only: Option<Vec<String>>,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize, JsonSchema)]
struct ApplyCodeActionInput {
    /// LSP `CodeAction` payload.
    action: JsonObject,
}

#[allow(dead_code)]
#[derive(Debug, Deserialize, JsonSchema)]
struct ApplyWorkspaceEditInput {
    /// LSP `WorkspaceEdit` payload.
    edit: JsonObject,
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // unwrap is permitted in tests (err-no-unwrap-prod)
    use super::*;
    use std::collections::BTreeSet;

    fn props_and_required(schema: &RootSchema) -> (BTreeSet<String>, BTreeSet<String>) {
        let value = serde_json::to_value(schema).expect("schema serializes to json");
        let props = value
            .get("properties")
            .and_then(serde_json::Value::as_object)
            .map(|map| map.keys().cloned().collect())
            .unwrap_or_default();
        let required = value
            .get("required")
            .and_then(serde_json::Value::as_array)
            .map(|arr| {
                arr.iter()
                    .filter_map(|v| v.as_str().map(str::to_owned))
                    .collect()
            })
            .unwrap_or_default();
        (props, required)
    }

    fn assert_shape(schema: &RootSchema, props: &[&str], required: &[&str]) {
        let (actual_props, actual_required) = props_and_required(schema);
        let expected_props: BTreeSet<String> = props.iter().map(|s| (*s).to_owned()).collect();
        let expected_required: BTreeSet<String> =
            required.iter().map(|s| (*s).to_owned()).collect();
        assert_eq!(actual_props, expected_props, "property set");
        assert_eq!(actual_required, expected_required, "required set");
    }

    // AC-plugin-catalog-09: ten specs with the right names and intents (proves
    // GC-plugin-catalog-04 — built against the declared deps only).
    #[test]
    fn ten_lsp_specs_with_intents() {
        let specs = plugin_tool_specs();
        assert_eq!(specs.len(), 10);

        let names: BTreeSet<&str> = specs.iter().map(|s| s.name.as_str()).collect();
        let expected: BTreeSet<&str> = [
            "lsp.hover",
            "lsp.find_definitions",
            "lsp.find_references",
            "lsp.diagnostics",
            "lsp.query_symbols",
            "lsp.rename",
            "lsp.format",
            "lsp.code_actions",
            "lsp.apply_code_action",
            "lsp.apply_workspace_edit",
        ]
        .into_iter()
        .collect();
        assert_eq!(names, expected);

        let write_tools: BTreeSet<&str> = specs
            .iter()
            .filter(|s| s.intent == Intent::WriteAllowed)
            .map(|s| s.name.as_str())
            .collect();
        let expected_writes: BTreeSet<&str> = [
            "lsp.rename",
            "lsp.format",
            "lsp.apply_code_action",
            "lsp.apply_workspace_edit",
        ]
        .into_iter()
        .collect();
        assert_eq!(write_tools, expected_writes);
    }

    // AC-plugin-catalog-07: the public surface is metadata + specs only, never a
    // Python-module loader (proves GC-plugin-catalog-01).
    #[test]
    fn no_module_import_surface() {
        for s in plugin_tool_specs() {
            assert!(!s.description.is_empty());
            assert!(s.name.as_str().starts_with("lsp."));
        }
    }

    // AC-plugin-catalog-10: normalized input-schema parity (field/optionality set
    // + defaults + the `lsp.format` name), not raw byte-equality. Proves
    // GC-plugin-catalog-07.
    #[test]
    fn lsp_input_schema_snapshots() {
        assert_shape(
            &schema_for!(HoverInput),
            &["file_path", "line", "character"],
            &["file_path", "line", "character"],
        );
        assert_shape(
            &schema_for!(FindDefinitionsInput),
            &["file_path", "line", "character"],
            &["file_path", "line", "character"],
        );
        assert_shape(
            &schema_for!(FindReferencesInput),
            &["file_path", "line", "character", "include_declaration"],
            &["file_path", "line", "character"],
        );
        assert_shape(
            &schema_for!(DiagnosticsInput),
            &["file_path", "wait_for_diagnostics"],
            &["file_path"],
        );
        assert_shape(
            &schema_for!(QuerySymbolsInput),
            &["query", "file_path"],
            &["query"],
        );
        assert_shape(
            &schema_for!(RenameInput),
            &["file_path", "line", "character", "new_name"],
            &["file_path", "line", "character", "new_name"],
        );
        assert_shape(
            &schema_for!(FormatInput),
            &["file_path", "options"],
            &["file_path"],
        );
        assert_shape(
            &schema_for!(CodeActionsInput),
            &[
                "file_path",
                "line",
                "character",
                "range",
                "diagnostics",
                "only",
            ],
            &["file_path"],
        );
        assert_shape(&schema_for!(ApplyCodeActionInput), &["action"], &["action"]);
        assert_shape(&schema_for!(ApplyWorkspaceEditInput), &["edit"], &["edit"]);

        // The `u32` choice is structural: `line` is a non-negative integer.
        let hover = serde_json::to_value(schema_for!(HoverInput)).unwrap();
        let line = &hover["properties"]["line"];
        assert_eq!(line["type"], serde_json::json!("integer"));
        assert!(
            line.get("minimum").is_some(),
            "u32 emits a non-negative minimum"
        );

        // GC-plugin-catalog-07: the name is `lsp.format`, never `lsp.format_document`.
        let specs = plugin_tool_specs();
        let names: Vec<&str> = specs.iter().map(|s| s.name.as_str()).collect();
        assert!(names.contains(&"lsp.format"));
        assert!(!names.iter().any(|n| n.contains("format_document")));

        // The two behavior-carrying defaults (a non-trivial `true`, and the
        // options object) are proven by a deserialize round-trip — the field-set
        // assertions above only prove optionality, not the default value.
        let refs: FindReferencesInput = serde_json::from_value(
            serde_json::json!({"file_path": "x", "line": 0, "character": 0}),
        )
        .unwrap();
        assert!(
            refs.include_declaration,
            "include_declaration defaults to true"
        );
        let fmt: FormatInput =
            serde_json::from_value(serde_json::json!({"file_path": "x"})).unwrap();
        assert_eq!(fmt.options, default_format_options());
    }
}
