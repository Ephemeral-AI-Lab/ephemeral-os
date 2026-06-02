//! [`PluginCatalogError`] — this crate's single library error enum.
//!
//! One `thiserror` enum (anchor §8, `err-thiserror-lib`); messages are
//! lowercase with no trailing punctuation (`err-lowercase-msg`) and `#[source]`
//! chains the underlying YAML / IO causes (`err-source-chain`). The granular
//! variants mirror the distinct `PluginManifestError` messages raised across
//! `plugins/core/manifest.py` and `plugins/core/discovery.py`.

use std::path::PathBuf;

/// Every failure mode of manifest parsing, path resolution, and catalog
/// discovery. `#[non_exhaustive]` because new validation rules may add variants
/// (`api-non-exhaustive`).
#[derive(Debug, thiserror::Error)]
#[non_exhaustive]
pub enum PluginCatalogError {
    /// The configured catalog root exists but is not a directory.
    #[error("catalog root is not a directory: {0}")]
    RootNotDir(PathBuf),
    /// No `plugin.md` was found under the plugin directory.
    #[error("plugin.md missing under {0}")]
    ManifestMissing(PathBuf),
    /// `plugin.md` lacks a `---`-delimited frontmatter block.
    #[error("plugin.md missing `---`-delimited frontmatter block in {0}")]
    MissingFrontmatter(PathBuf),
    /// The frontmatter is not valid YAML.
    #[error("plugin.md frontmatter is not valid yaml in {path}")]
    Frontmatter {
        /// The offending `plugin.md` path.
        path: PathBuf,
        /// The underlying YAML parse error.
        #[source]
        cause: serde_yaml::Error,
    },
    /// The frontmatter parsed but is not a YAML mapping.
    #[error("plugin.md frontmatter is not a yaml mapping in {0}")]
    NotMapping(PathBuf),
    /// A required string field is missing, empty, or not a string.
    #[error("plugin.md {field} must be a non-empty string in {path}")]
    MissingField {
        /// The offending `plugin.md` path.
        path: PathBuf,
        /// The field (or `tools[i].name`-style location) that failed.
        field: String,
    },
    /// `tools` is absent, not a list, or empty.
    #[error("plugin.md tools must be a non-empty list in {0}")]
    EmptyTools(PathBuf),
    /// A plugin name does not match `^[a-z][a-z0-9_]*$`.
    #[error("invalid plugin name {0:?}")]
    InvalidName(String),
    /// The manifest `name` does not equal the plugin directory name.
    #[error("plugin name {name:?} does not match directory {dir:?}")]
    NameDirMismatch {
        /// The declared manifest name.
        name: String,
        /// The actual directory name.
        dir: String,
    },
    /// A tool name does not start with the `<plugin_name>.` prefix.
    #[error("plugin tool name {name:?} must start with {prefix:?}")]
    ToolPrefix {
        /// The offending tool name.
        name: String,
        /// The required `<plugin_name>.` prefix.
        prefix: String,
    },
    /// Two tools within one manifest declare the same name.
    #[error("duplicate tool name {0:?}")]
    DuplicateTool(String),
    /// A declared path resolves outside the plugin directory.
    #[error("path escapes plugin dir: {0:?}")]
    PathEscape(String),
    /// A declared path resolves under the plugin dir but does not exist.
    #[error("declared path does not exist: {0}")]
    PathMissing(PathBuf),
    /// `kind` is present but is not a non-empty string.
    #[error("plugin kind must be a non-empty string when set, in {0}")]
    KindNotString(PathBuf),
    /// `kind` is a string but not one of the recognized [`PluginKind`] values.
    ///
    /// [`PluginKind`]: crate::PluginKind
    #[error("plugin kind {0:?} is not recognized")]
    UnknownKind(String),
    /// Two catalog folders declare the same plugin name.
    #[error("duplicate plugin name {name:?} in {first} and {second}")]
    DuplicatePlugin {
        /// The colliding plugin name.
        name: String,
        /// The directory of the first manifest with this name.
        first: PathBuf,
        /// The directory of the second manifest with this name.
        second: PathBuf,
    },
    /// A filesystem read failed.
    #[error("failed to read {path}")]
    Io {
        /// The path that could not be read.
        path: PathBuf,
        /// The underlying IO error.
        #[source]
        cause: std::io::Error,
    },
}
