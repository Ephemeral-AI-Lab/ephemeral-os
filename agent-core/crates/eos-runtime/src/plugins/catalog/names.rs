//! Validated newtypes: [`PluginName`], [`PluginToolName`], [`PluginResolvedPath`].
//!
//! Each is a parse-don't-validate boundary (`api-parse-dont-validate`,
//! `type-newtype-validated`). They derive `Serialize` + `JsonSchema` only —
//! **never** `Deserialize`: a derived `Deserialize` would be an unvalidated
//! public constructor able to mint a [`PluginResolvedPath`] holding `../evil`
//! or a [`PluginName`] violating `^[a-z][a-z0-9_]*$`. Withholding it makes the
//! validating constructors the *sole* way in, which is what guarantees a parsed
//! [`PluginResolvedPath`] cannot escape its plugin directory
//! (GC-plugin-catalog-06).

use std::path::{Component, Path, PathBuf};

use schemars::JsonSchema;
use serde::Serialize;

use super::error::PluginCatalogError;

/// A plugin folder + manifest name. Matches `^[a-z][a-z0-9_]*$` and (when
/// produced by the manifest parser) equals the plugin directory name
/// (`plugins/core/manifest.py` lines 35, 105-115).
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, JsonSchema)]
#[serde(transparent)]
#[schemars(transparent)]
pub struct PluginName(String);

impl PluginName {
    /// Parse a plugin name, enforcing the `^[a-z][a-z0-9_]*$` pattern with a
    /// manual ASCII char-class check (no `regex` dependency, mirroring the
    /// skills loader). Returns [`PluginCatalogError::InvalidName`] otherwise.
    ///
    /// # Errors
    /// [`PluginCatalogError::InvalidName`] when the input does not match the
    /// pattern.
    pub fn parse(s: impl Into<String>) -> Result<Self, PluginCatalogError> {
        let s = s.into();
        if is_valid_plugin_name(&s) {
            Ok(Self(s))
        } else {
            Err(PluginCatalogError::InvalidName(s))
        }
    }

    /// The underlying name as a string slice.
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// A validated `<plugin_name>.<suffix>` tool name. The `<plugin_name>.` prefix,
/// uniqueness, and non-emptiness are enforced by the manifest parser before
/// construction (`plugins/core/manifest.py` lines 179-199); this newtype simply
/// carries the validated result.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, JsonSchema)]
#[serde(transparent)]
#[schemars(transparent)]
pub struct PluginToolName(String);

impl PluginToolName {
    /// Wrap an already-validated tool name. Internal: the only callers are the
    /// manifest parser (after the prefix/dup checks) and the built-in
    /// [`plugin_tool_specs`](super::plugin_tool_specs) compile-time constants.
    #[must_use]
    pub(crate) fn new(s: impl Into<String>) -> Self {
        Self(s.into())
    }

    /// The underlying tool name as a string slice.
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// A path declared in `plugin.md`, resolved and proven to live **under** the
/// plugin directory (no `..` escape) — the security invariant from
/// `plugins/core/manifest.py` `_resolve_under`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, JsonSchema)]
pub struct PluginResolvedPath(PathBuf);

impl PluginResolvedPath {
    /// Resolve `raw` against `plugin_dir` and prove it stays under it.
    ///
    /// `plugin_dir` is expected to be absolute (the manifest parser canonicalizes
    /// it first). The join mirrors Python's `plugin_dir / raw` (an absolute `raw`
    /// replaces the base), then the result is lexically normalized (`..`/`.`
    /// collapsed without touching the filesystem, so existence is checked
    /// separately by the caller) and required to start with `plugin_dir`.
    ///
    /// # Errors
    /// [`PluginCatalogError::PathEscape`] when the resolved path is not under
    /// `plugin_dir`.
    pub(crate) fn resolve_under(plugin_dir: &Path, raw: &str) -> Result<Self, PluginCatalogError> {
        let normalized = lexical_normalize(&plugin_dir.join(raw));
        if normalized.starts_with(plugin_dir) {
            Ok(Self(normalized))
        } else {
            Err(PluginCatalogError::PathEscape(raw.to_owned()))
        }
    }

    /// The resolved absolute path.
    #[must_use]
    pub fn as_path(&self) -> &Path {
        &self.0
    }

    /// Consume the newtype, returning the inner [`PathBuf`].
    #[must_use]
    pub(crate) fn into_path_buf(self) -> PathBuf {
        self.0
    }
}

/// `^[a-z][a-z0-9_]*$`, checked without `regex`.
fn is_valid_plugin_name(s: &str) -> bool {
    let mut chars = s.chars();
    match chars.next() {
        Some(c) if c.is_ascii_lowercase() => {}
        _ => return false,
    }
    chars.all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_')
}

/// Collapse `.` and `..` components without resolving symlinks or requiring the
/// path to exist (Python's `Path.resolve(strict=False)` then `relative_to`).
/// This catches every `..` escape, which is the invariant GC-plugin-catalog-06
/// guards. Lexical (not `canonicalize`) is the correct choice on two counts:
/// `canonicalize` would report a non-existent escaping path (`../evil.py`) as
/// `NotFound`, which AC-04 requires to be `PathEscape`; and the residual gap (a
/// symlink *inside* the dir pointing out, which `canonicalize` would catch) is
/// bounded because this crate only validates and stores the path — it never reads
/// or executes it (GC-plugin-catalog-05); the sandbox RPC, not this path, does IO.
fn lexical_normalize(p: &Path) -> PathBuf {
    let mut out = PathBuf::new();
    for comp in p.components() {
        match comp {
            Component::ParentDir => {
                out.pop();
            }
            Component::CurDir => {}
            Component::Prefix(_) | Component::RootDir | Component::Normal(_) => {
                out.push(comp.as_os_str());
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    // AC-plugin-catalog-04: a `module` of `../evil.py` (or an absolute path
    // outside) escapes the plugin dir, and `resolve_under` never returns a path
    // outside `plugin_dir` (proves GC-plugin-catalog-06).
    #[test]
    fn rejects_path_escape() {
        let plugin_dir = Path::new("/catalog/lsp");

        // A clean relative path resolves under the dir.
        let ok = PluginResolvedPath::resolve_under(plugin_dir, "tools/hover.py")
            .expect("under-dir path resolves");
        assert!(ok.as_path().starts_with(plugin_dir));
        assert_eq!(ok.as_path(), Path::new("/catalog/lsp/tools/hover.py"));

        // `..` escape is rejected.
        let escape = PluginResolvedPath::resolve_under(plugin_dir, "../evil.py");
        assert!(matches!(escape, Err(PluginCatalogError::PathEscape(_))));

        // A nested `..` that still escapes is rejected.
        let nested = PluginResolvedPath::resolve_under(plugin_dir, "tools/../../evil.py");
        assert!(matches!(nested, Err(PluginCatalogError::PathEscape(_))));

        // An absolute path outside is rejected (join replaces, like Python `/`).
        let abs = PluginResolvedPath::resolve_under(plugin_dir, "/etc/passwd");
        assert!(matches!(abs, Err(PluginCatalogError::PathEscape(_))));

        // A `..` that stays inside is allowed (resolves back under the dir).
        let inside = PluginResolvedPath::resolve_under(plugin_dir, "tools/../runtime/server.py")
            .expect("in-dir traversal resolves");
        assert_eq!(
            inside.as_path(),
            Path::new("/catalog/lsp/runtime/server.py")
        );
    }

    #[test]
    fn plugin_name_pattern() {
        assert!(PluginName::parse("lsp").is_ok());
        assert!(PluginName::parse("py_lsp2").is_ok());
        // Leading digit, uppercase, hyphen, leading underscore, and empty all fail.
        for bad in ["2lsp", "LSP", "ls-p", "_lsp", ""] {
            assert!(
                matches!(
                    PluginName::parse(bad),
                    Err(PluginCatalogError::InvalidName(_))
                ),
                "expected {bad:?} to be rejected"
            );
        }
    }
}
