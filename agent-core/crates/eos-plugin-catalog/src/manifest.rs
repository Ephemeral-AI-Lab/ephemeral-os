//! [`PluginManifest`] parsing: the two-stage parse from `plugin.md` frontmatter.
//!
//! Ports `plugins/core/manifest.py`. Validation is two-stage (`api-parse-dont-validate`):
//! deserialize the frontmatter into a tolerant [`RawManifest`] DTO (the only
//! `Deserialize` target here), then **validate-into** the invariant-bearing
//! [`PluginManifest`] with `plugin_dir` context. `RawManifest`'s fields are
//! `Option<serde_yaml::Value>` rather than `Option<String>` so that a wrong-typed
//! field (e.g. `name: 123`) yields the granular [`PluginCatalogError::MissingField`]
//! / [`PluginCatalogError::KindNotString`] that Python's `isinstance`-style
//! `_require_str`/`_parse_kind` produce, instead of an opaque serde error — the
//! source is the tie-breaker over the spec's `Option<String>` sketch.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::error::PluginCatalogError;
use crate::frontmatter::split_frontmatter;
use crate::names::{PluginName, PluginResolvedPath, PluginToolName};

/// The declared `kind` of a plugin (was `ALLOWED_PLUGIN_KINDS`, manifest.py
/// 45-54). `#[non_exhaustive]` — the plan reserves room for new kinds.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
#[non_exhaustive]
pub enum PluginKind {
    /// A language server (e.g. the bundled LSP plugin).
    LanguageServer,
    /// A code formatter.
    Formatter,
    /// A source indexer.
    Indexer,
    /// A long-lived build daemon.
    BuildDaemon,
    /// A bridge to an external MCP server.
    McpBridge,
    /// An uncategorized plugin (the audit fallback when `kind` is unset).
    Custom,
}

impl PluginKind {
    /// Parse a recognized kind string, returning
    /// [`PluginCatalogError::UnknownKind`] for an unrecognized value.
    pub(crate) fn parse(s: &str) -> Result<Self, PluginCatalogError> {
        match s {
            "language_server" => Ok(Self::LanguageServer),
            "formatter" => Ok(Self::Formatter),
            "indexer" => Ok(Self::Indexer),
            "build_daemon" => Ok(Self::BuildDaemon),
            "mcp_bridge" => Ok(Self::McpBridge),
            "custom" => Ok(Self::Custom),
            other => Err(PluginCatalogError::UnknownKind(other.to_owned())),
        }
    }
}

/// One declared tool in a manifest (manifest.py 57-62).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, JsonSchema)]
#[non_exhaustive]
pub struct ToolEntry {
    /// The validated `<plugin_name>.<suffix>` tool name.
    pub name: PluginToolName,
    /// The tool module path, resolved under the plugin directory and proven to
    /// exist on disk (validated only — **never** imported or executed).
    pub module: PluginResolvedPath,
}

/// A parsed and validated `plugin.md` (manifest.py 65-77). An immutable value
/// type produced **only** by [`parse_plugin_manifest`] — it derives no
/// `Deserialize` (it is not a wire-input DTO; see [`RawManifest`]).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, JsonSchema)]
#[non_exhaustive]
pub struct PluginManifest {
    /// The plugin name (equals the plugin directory name).
    pub name: PluginName,
    /// A human-readable description.
    pub description: String,
    /// The non-empty list of declared tools.
    pub tools: Vec<ToolEntry>,
    /// The optional setup script path (validated, not executed).
    pub setup: Option<PluginResolvedPath>,
    /// The optional in-sandbox runtime entrypoint path (validated, not executed).
    pub runtime: Option<PluginResolvedPath>,
    /// The absolute, canonicalized plugin directory.
    pub source_dir: PathBuf,
    /// The trimmed markdown body after the frontmatter (informational).
    pub body: String,
    /// The optional declared kind (`None` when unset).
    pub kind: Option<PluginKind>,
}

/// Tolerant wire-input DTO: the only `Deserialize` target in this crate. Each
/// field is `Option<serde_yaml::Value>` so a wrong-typed field surfaces as a
/// granular [`PluginCatalogError`] during validation rather than collapsing into
/// one opaque serde error. Unknown frontmatter keys are ignored (no
/// `deny_unknown_fields`), matching Python's `dict.get`.
#[derive(Debug, Deserialize)]
pub(crate) struct RawManifest {
    #[serde(default)]
    name: Option<serde_yaml::Value>,
    #[serde(default)]
    description: Option<serde_yaml::Value>,
    #[serde(default)]
    tools: Option<serde_yaml::Value>,
    #[serde(default)]
    setup: Option<serde_yaml::Value>,
    #[serde(default)]
    runtime: Option<serde_yaml::Value>,
    #[serde(default)]
    kind: Option<serde_yaml::Value>,
}

/// Parse `<plugin_dir>/plugin.md` and validate its schema.
///
/// # Errors
/// A [`PluginCatalogError`] variant for each distinct failure (missing manifest,
/// bad frontmatter, name/dir mismatch, tool prefix/duplicate, path escape/missing,
/// kind validation), mirroring `parse_plugin_manifest` in manifest.py.
pub(crate) fn parse_plugin_manifest(
    plugin_dir: &Path,
) -> Result<PluginManifest, PluginCatalogError> {
    let plugin_dir = plugin_dir
        .canonicalize()
        .map_err(|cause| PluginCatalogError::Io {
            path: plugin_dir.to_owned(),
            cause,
        })?;
    let manifest_path = plugin_dir.join("plugin.md");
    if !manifest_path.is_file() {
        return Err(PluginCatalogError::ManifestMissing(plugin_dir));
    }
    let text = std::fs::read_to_string(&manifest_path).map_err(|cause| PluginCatalogError::Io {
        path: manifest_path.clone(),
        cause,
    })?;
    let (frontmatter, body) = split_frontmatter(&text)
        .ok_or_else(|| PluginCatalogError::MissingFrontmatter(manifest_path.clone()))?;

    let value: serde_yaml::Value =
        serde_yaml::from_str(&frontmatter).map_err(|cause| PluginCatalogError::Frontmatter {
            path: manifest_path.clone(),
            cause,
        })?;
    // `yaml.safe_load(...) or {}`: an empty frontmatter is an empty mapping.
    let value = if value.is_null() {
        serde_yaml::Value::Mapping(serde_yaml::Mapping::new())
    } else {
        value
    };
    if !value.is_mapping() {
        return Err(PluginCatalogError::NotMapping(manifest_path));
    }
    let raw: RawManifest =
        serde_yaml::from_value(value).map_err(|cause| PluginCatalogError::Frontmatter {
            path: manifest_path.clone(),
            cause,
        })?;

    let dir_name = plugin_dir
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or_default()
        .to_owned();

    let name = require_str(raw.name.as_ref(), "name", &manifest_path)?;
    let plugin_name = PluginName::parse(name.clone())?;
    if name != dir_name {
        return Err(PluginCatalogError::NameDirMismatch {
            name,
            dir: dir_name,
        });
    }

    let description = require_str(raw.description.as_ref(), "description", &manifest_path)?;
    let tools = parse_tools(raw.tools.as_ref(), &plugin_dir, &manifest_path, &name)?;
    let setup = resolve_setup(raw.setup.as_ref(), &plugin_dir, &manifest_path)?;
    let runtime =
        resolve_optional_path(raw.runtime.as_ref(), "runtime", &plugin_dir, &manifest_path)?;
    let kind = parse_kind(raw.kind.as_ref(), &manifest_path)?;

    Ok(PluginManifest {
        name: plugin_name,
        description,
        tools,
        setup,
        runtime,
        source_dir: plugin_dir,
        body: body.trim().to_owned(),
        kind,
    })
}

/// Extract a required non-empty string field, else [`PluginCatalogError::MissingField`].
fn require_str(
    field: Option<&serde_yaml::Value>,
    name: &str,
    path: &Path,
) -> Result<String, PluginCatalogError> {
    string_value(field).ok_or_else(|| PluginCatalogError::MissingField {
        path: path.to_owned(),
        field: name.to_owned(),
    })
}

/// `Some(trimmed)` iff the value is a non-empty string (Python `isinstance(str)`
/// + `.strip()`), else `None`.
fn string_value(v: Option<&serde_yaml::Value>) -> Option<String> {
    match v {
        Some(serde_yaml::Value::String(s)) if !s.trim().is_empty() => Some(s.trim().to_owned()),
        _ => None,
    }
}

fn parse_tools(
    tools: Option<&serde_yaml::Value>,
    plugin_dir: &Path,
    manifest_path: &Path,
    plugin_name: &str,
) -> Result<Vec<ToolEntry>, PluginCatalogError> {
    let seq = match tools {
        Some(serde_yaml::Value::Sequence(seq)) if !seq.is_empty() => seq,
        _ => return Err(PluginCatalogError::EmptyTools(manifest_path.to_owned())),
    };
    let prefix = format!("{plugin_name}.");
    let mut seen: BTreeSet<String> = BTreeSet::new();
    let mut entries = Vec::with_capacity(seq.len());
    for (index, entry) in seq.iter().enumerate() {
        let map = entry.as_mapping();
        let tool_name = string_value(map.and_then(|m| m.get("name"))).ok_or_else(|| {
            PluginCatalogError::MissingField {
                path: manifest_path.to_owned(),
                field: format!("tools[{index}].name"),
            }
        })?;
        if !tool_name.starts_with(&prefix) {
            return Err(PluginCatalogError::ToolPrefix {
                name: tool_name,
                prefix,
            });
        }
        if !seen.insert(tool_name.clone()) {
            return Err(PluginCatalogError::DuplicateTool(tool_name));
        }
        let module_raw = string_value(map.and_then(|m| m.get("module"))).ok_or_else(|| {
            PluginCatalogError::MissingField {
                path: manifest_path.to_owned(),
                field: format!("tools[{index}].module"),
            }
        })?;
        let module = PluginResolvedPath::resolve_under(plugin_dir, &module_raw)?;
        if !module.as_path().is_file() {
            return Err(PluginCatalogError::PathMissing(module.into_path_buf()));
        }
        entries.push(ToolEntry {
            name: PluginToolName::new(tool_name),
            module,
        });
    }
    Ok(entries)
}

/// Resolve `setup`, defaulting to `setup.sh` iff it exists when unset
/// (manifest.py 218-239).
fn resolve_setup(
    raw: Option<&serde_yaml::Value>,
    plugin_dir: &Path,
    manifest_path: &Path,
) -> Result<Option<PluginResolvedPath>, PluginCatalogError> {
    // The unset case defaults to `setup.sh` iff present; the set-string and error
    // arms are identical to `resolve_optional_path`, so delegate them (DRY).
    if matches!(raw, None | Some(serde_yaml::Value::Null)) {
        let default = PluginResolvedPath::resolve_under(plugin_dir, "setup.sh")?;
        return Ok(default.as_path().is_file().then_some(default));
    }
    resolve_optional_path(raw, "setup", plugin_dir, manifest_path)
}

/// Resolve an optional declared path (`runtime`); `None` when unset, error when
/// set-but-not-a-non-empty-string (manifest.py 242-263).
fn resolve_optional_path(
    raw: Option<&serde_yaml::Value>,
    field: &str,
    plugin_dir: &Path,
    manifest_path: &Path,
) -> Result<Option<PluginResolvedPath>, PluginCatalogError> {
    match raw {
        None | Some(serde_yaml::Value::Null) => Ok(None),
        Some(serde_yaml::Value::String(s)) if !s.trim().is_empty() => {
            resolve_existing(plugin_dir, s.trim())
        }
        _ => Err(PluginCatalogError::MissingField {
            path: manifest_path.to_owned(),
            field: field.to_owned(),
        }),
    }
}

/// Resolve a declared path under the plugin dir and require it to exist.
fn resolve_existing(
    plugin_dir: &Path,
    raw: &str,
) -> Result<Option<PluginResolvedPath>, PluginCatalogError> {
    let resolved = PluginResolvedPath::resolve_under(plugin_dir, raw)?;
    if resolved.as_path().is_file() {
        Ok(Some(resolved))
    } else {
        Err(PluginCatalogError::PathMissing(resolved.into_path_buf()))
    }
}

/// Validate the optional `kind` field (manifest.py 140-164).
fn parse_kind(
    raw: Option<&serde_yaml::Value>,
    manifest_path: &Path,
) -> Result<Option<PluginKind>, PluginCatalogError> {
    match raw {
        None | Some(serde_yaml::Value::Null) => Ok(None),
        Some(serde_yaml::Value::String(s)) if !s.trim().is_empty() => {
            PluginKind::parse(s.trim()).map(Some)
        }
        _ => Err(PluginCatalogError::KindNotString(manifest_path.to_owned())),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::support::{make_plugin, temp_root};

    const LSP_MANIFEST: &str = "\
---
name: lsp
description: Pyright-backed LSP tools for Python.
kind: language_server
tools:
  - name: lsp.hover
    module: tools/hover.py
  - name: lsp.find_definitions
    module: tools/find_definitions.py
  - name: lsp.find_references
    module: tools/find_references.py
  - name: lsp.diagnostics
    module: tools/diagnostics.py
  - name: lsp.query_symbols
    module: tools/query_symbols.py
  - name: lsp.apply_workspace_edit
    module: tools/apply_workspace_edit.py
  - name: lsp.rename
    module: tools/rename.py
  - name: lsp.format
    module: tools/format.py
  - name: lsp.code_actions
    module: tools/code_actions.py
  - name: lsp.apply_code_action
    module: tools/apply_code_action.py
setup: setup.sh
runtime: runtime/server.py
---

# LSP Plugin

Pyright-backed Python language tools.
";

    const LSP_FILES: &[&str] = &[
        "tools/hover.py",
        "tools/find_definitions.py",
        "tools/find_references.py",
        "tools/diagnostics.py",
        "tools/query_symbols.py",
        "tools/apply_workspace_edit.py",
        "tools/rename.py",
        "tools/format.py",
        "tools/code_actions.py",
        "tools/apply_code_action.py",
        "setup.sh",
        "runtime/server.py",
    ];

    // AC-plugin-catalog-01: the real LSP manifest parses to the expected shape.
    #[test]
    fn parses_lsp_manifest() {
        let root = temp_root("lsp_ok");
        let dir = make_plugin(&root, "lsp", Some(LSP_MANIFEST), LSP_FILES);
        let manifest = parse_plugin_manifest(&dir).expect("parses");
        assert_eq!(manifest.name.as_str(), "lsp");
        assert_eq!(manifest.kind, Some(PluginKind::LanguageServer));
        assert_eq!(manifest.tools.len(), 10);
        for tool in &manifest.tools {
            assert!(tool.name.as_str().starts_with("lsp."));
            assert!(tool.module.as_path().is_file());
        }
        assert!(manifest.setup.is_some());
        assert!(manifest.runtime.is_some());
        assert!(manifest.body.contains("LSP Plugin"));
        let _ = std::fs::remove_dir_all(&root);
    }

    // AC-plugin-catalog-02: the manifest rejection paths.
    #[test]
    fn rejects_bad_names_prefixes_and_duplicates() {
        let root = temp_root("reject");

        let d1 = make_plugin(
            &root,
            "lsp",
            Some("---\nname: other\ndescription: d\ntools:\n  - name: other.x\n    module: tools/x.py\n---\n"),
            &[],
        );
        assert!(matches!(
            parse_plugin_manifest(&d1),
            Err(PluginCatalogError::NameDirMismatch { .. })
        ));

        let d2 = make_plugin(
            &root,
            "alpha",
            Some("---\nname: alpha\ndescription: d\ntools:\n  - name: beta.x\n    module: tools/x.py\n---\n"),
            &[],
        );
        assert!(matches!(
            parse_plugin_manifest(&d2),
            Err(PluginCatalogError::ToolPrefix { .. })
        ));

        // The first tool's module must exist so the loop reaches the duplicate
        // before any per-tool module-existence check fires (parse order matches
        // Python's `_parse_tools`).
        let d3 = make_plugin(
            &root,
            "gamma",
            Some("---\nname: gamma\ndescription: d\ntools:\n  - name: gamma.x\n    module: a.py\n  - name: gamma.x\n    module: b.py\n---\n"),
            &["a.py"],
        );
        assert!(matches!(
            parse_plugin_manifest(&d3),
            Err(PluginCatalogError::DuplicateTool(_))
        ));

        let d4 = make_plugin(&root, "seqp", Some("---\n- a\n- b\n---\n"), &[]);
        assert!(matches!(
            parse_plugin_manifest(&d4),
            Err(PluginCatalogError::NotMapping(_))
        ));

        let d5 = make_plugin(
            &root,
            "empty",
            Some("---\nname: empty\ndescription: d\ntools: []\n---\n"),
            &[],
        );
        assert!(matches!(
            parse_plugin_manifest(&d5),
            Err(PluginCatalogError::EmptyTools(_))
        ));

        let d6 = make_plugin(
            &root,
            "noname",
            Some("---\ndescription: d\ntools:\n  - name: noname.x\n    module: tools/x.py\n---\n"),
            &[],
        );
        assert!(matches!(
            parse_plugin_manifest(&d6),
            Err(PluginCatalogError::MissingField { .. })
        ));

        // No `---` fence at all -> MissingFrontmatter (manifest.py 87-92).
        let d7 = make_plugin(&root, "nofence", Some("name: nofence\n"), &[]);
        assert!(matches!(
            parse_plugin_manifest(&d7),
            Err(PluginCatalogError::MissingFrontmatter(_))
        ));

        // Fenced but malformed YAML -> Frontmatter (manifest.py 96-99).
        let d8 = make_plugin(&root, "badyaml", Some("---\nname: [unterminated\n---\n"), &[]);
        assert!(matches!(
            parse_plugin_manifest(&d8),
            Err(PluginCatalogError::Frontmatter { .. })
        ));

        let _ = std::fs::remove_dir_all(&root);
    }

    // AC-plugin-catalog-03: a declared path that is under-dir but does not exist.
    #[test]
    fn missing_declared_path_errors() {
        let root = temp_root("missing_path");
        let dir = make_plugin(
            &root,
            "alpha",
            Some("---\nname: alpha\ndescription: d\ntools:\n  - name: alpha.x\n    module: tools/x.py\n---\n"),
            &[],
        );
        assert!(matches!(
            parse_plugin_manifest(&dir),
            Err(PluginCatalogError::PathMissing(_))
        ));
        let _ = std::fs::remove_dir_all(&root);
    }

    // AC-plugin-catalog-05: kind validation (unset -> None; known; unknown -> error).
    #[test]
    fn kind_enum_validation() {
        let root = temp_root("kind");
        let manifest = |kind_line: &str| {
            format!(
                "---\nname: k\ndescription: d\n{kind_line}tools:\n  - name: k.x\n    module: tools/x.py\n---\n"
            )
        };

        let d_unset = make_plugin(&root, "k", Some(&manifest("")), &["tools/x.py"]);
        assert_eq!(parse_plugin_manifest(&d_unset).expect("parses").kind, None);

        let d_known = make_plugin(&root, "k", Some(&manifest("kind: formatter\n")), &["tools/x.py"]);
        assert_eq!(
            parse_plugin_manifest(&d_known).expect("parses").kind,
            Some(PluginKind::Formatter)
        );

        let d_unknown = make_plugin(&root, "k", Some(&manifest("kind: wizard\n")), &["tools/x.py"]);
        assert!(matches!(
            parse_plugin_manifest(&d_unknown),
            Err(PluginCatalogError::UnknownKind(_))
        ));

        // A present-but-non-string kind -> KindNotString (manifest.py 154-157).
        let d_nonstr = make_plugin(&root, "k", Some(&manifest("kind: 123\n")), &["tools/x.py"]);
        assert!(matches!(
            parse_plugin_manifest(&d_nonstr),
            Err(PluginCatalogError::KindNotString(_))
        ));

        let _ = std::fs::remove_dir_all(&root);
    }

    // AC-plugin-catalog-01 (setup default branch): an unset `setup:` resolves to
    // `setup.sh` when that file exists on disk (manifest.py 224-226).
    #[test]
    fn setup_defaults_to_setup_sh_when_present() {
        let root = temp_root("setup_default");
        let dir = make_plugin(
            &root,
            "s",
            Some("---\nname: s\ndescription: d\ntools:\n  - name: s.x\n    module: tools/x.py\n---\n"),
            &["tools/x.py", "setup.sh"],
        );
        let manifest = parse_plugin_manifest(&dir).expect("parses");
        let setup = manifest.setup.expect("setup defaults to setup.sh");
        assert!(setup.as_path().ends_with("setup.sh"));
        let _ = std::fs::remove_dir_all(&root);
    }
}
