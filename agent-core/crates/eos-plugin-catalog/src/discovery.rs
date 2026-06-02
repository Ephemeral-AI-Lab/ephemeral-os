//! [`PluginCatalog`] — the immutable, discovered plugin registry.
//!
//! Ports `plugins/core/discovery.py`. Discovery is a single synchronous
//! `read_dir` of the configured catalog root, run **once** at the composition
//! root (anchor §7). Determinism comes from the `BTreeMap<PluginName, _>` key
//! ordering, replacing Python's explicit `sorted(...)`. The
//! `DEFAULT_CATALOG_DIR`/`default_catalog_dir` `__file__` derivation is dropped
//! (GC-plugin-catalog-02): the root arrives as a `&Path` resolved by
//! `eos-config`.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use crate::error::PluginCatalogError;
use crate::manifest::{parse_plugin_manifest, PluginManifest};
use crate::names::PluginName;

/// The static catalog: every validated `<root>/<name>/plugin.md`, keyed (and
/// thus ordered) by [`PluginName`]. This is the `PluginCatalog` seam (anchor
/// §6); extension happens by adding plugin folders, not by adding a trait.
#[derive(Debug, Clone, Default)]
pub struct PluginCatalog {
    plugins: BTreeMap<PluginName, PluginManifest>,
}

impl PluginCatalog {
    /// Discover and validate every plugin under `catalog_root`.
    ///
    /// A non-existent root yields an **empty** catalog (Python returns `[]`); a
    /// root that exists but is not a directory is a config error and fails fast
    /// with [`PluginCatalogError::RootNotDir`]. Folders without a `plugin.md`,
    /// dot-folders, and `__pycache__` are skipped silently.
    ///
    /// # Errors
    /// [`PluginCatalogError::RootNotDir`] for a non-directory root,
    /// [`PluginCatalogError::DuplicatePlugin`] for a repeated plugin name, plus
    /// any manifest-validation error from [`parse_plugin_manifest`].
    pub fn discover_under(catalog_root: &Path) -> Result<Self, PluginCatalogError> {
        if !catalog_root.exists() {
            return Ok(Self::default());
        }
        if !catalog_root.is_dir() {
            return Err(PluginCatalogError::RootNotDir(catalog_root.to_owned()));
        }

        let mut candidates: Vec<PathBuf> = std::fs::read_dir(catalog_root)
            .map_err(|cause| PluginCatalogError::Io {
                path: catalog_root.to_owned(),
                cause,
            })?
            .filter_map(Result::ok)
            .map(|entry| entry.path())
            .filter(|path| is_candidate_dir(path))
            .collect();
        candidates.sort();

        let mut plugins: BTreeMap<PluginName, PluginManifest> = BTreeMap::new();
        for dir in candidates {
            if !dir.join("plugin.md").is_file() {
                continue;
            }
            let manifest = parse_plugin_manifest(&dir)?;
            if let Some(existing) = plugins.get(&manifest.name) {
                return Err(PluginCatalogError::DuplicatePlugin {
                    name: manifest.name.as_str().to_owned(),
                    first: existing.source_dir.clone(),
                    second: manifest.source_dir.clone(),
                });
            }
            plugins.insert(manifest.name.clone(), manifest);
        }
        Ok(Self { plugins })
    }

    /// Look up a manifest by plugin name.
    #[must_use]
    pub fn get(&self, name: &PluginName) -> Option<&PluginManifest> {
        self.plugins.get(name)
    }

    /// Iterate manifests in [`PluginName`] order.
    pub fn manifests(&self) -> impl Iterator<Item = &PluginManifest> {
        self.plugins.values()
    }

    /// The number of discovered plugins.
    #[must_use]
    pub fn len(&self) -> usize {
        self.plugins.len()
    }

    /// Whether the catalog is empty.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.plugins.is_empty()
    }
}

/// A discovery candidate: a directory whose name is neither a dot-folder nor
/// `__pycache__` (discovery.py 63-73). `is_dir` follows symlinks.
fn is_candidate_dir(path: &Path) -> bool {
    if !path.is_dir() {
        return false;
    }
    match path.file_name().and_then(|n| n.to_str()) {
        Some(name) => !name.starts_with('.') && name != "__pycache__",
        None => false,
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // unwrap is permitted in tests (err-no-unwrap-prod)
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};

    fn temp_root(tag: &str) -> PathBuf {
        static COUNTER: AtomicU64 = AtomicU64::new(0);
        let n = COUNTER.fetch_add(1, Ordering::Relaxed);
        let dir = std::env::temp_dir().join(format!(
            "eos_plugin_catalog_discovery_{}_{tag}_{n}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).expect("create temp root");
        dir
    }

    /// Create `<root>/<name>/` with a one-tool manifest and its module file.
    fn make_plugin(root: &Path, name: &str) -> PathBuf {
        let dir = root.join(name);
        std::fs::create_dir_all(dir.join("tools")).expect("create plugin dir");
        let manifest = format!(
            "---\nname: {name}\ndescription: d\ntools:\n  - name: {name}.x\n    module: tools/x.py\n---\n"
        );
        std::fs::write(dir.join("plugin.md"), manifest).expect("write plugin.md");
        std::fs::write(dir.join("tools/x.py"), b"# stub\n").expect("write module");
        dir
    }

    // AC-plugin-catalog-06: sorted discovery, skip rules, empty/file roots
    // (proves GC-plugin-catalog-02).
    #[test]
    fn discovers_sorted_dedup_and_roots() {
        let root = temp_root("ok");
        make_plugin(&root, "bravo");
        make_plugin(&root, "alpha");
        // Skipped: a dot-folder, __pycache__, and a folder without plugin.md.
        std::fs::create_dir_all(root.join(".hidden")).unwrap();
        std::fs::create_dir_all(root.join("__pycache__")).unwrap();
        std::fs::create_dir_all(root.join("not_a_plugin")).unwrap();

        let catalog = PluginCatalog::discover_under(&root).expect("discovers");
        let names: Vec<&str> = catalog.manifests().map(|m| m.name.as_str()).collect();
        assert_eq!(names, vec!["alpha", "bravo"]); // BTreeMap order
        assert_eq!(catalog.len(), 2);
        assert!(catalog.get(&PluginName::parse("alpha").unwrap()).is_some());

        // A non-existent root yields an empty catalog.
        let missing = root.join("does_not_exist");
        assert!(PluginCatalog::discover_under(&missing)
            .expect("missing root is empty")
            .is_empty());

        // A file-as-root fails fast.
        let file_root = root.join("alpha/plugin.md");
        assert!(matches!(
            PluginCatalog::discover_under(&file_root),
            Err(PluginCatalogError::RootNotDir(_))
        ));

        let _ = std::fs::remove_dir_all(&root);
    }

    // AC-plugin-catalog-06 (duplicate sub-case): the `name == dir` invariant makes
    // duplicate names unreachable via sibling dirs, but a symlinked folder that
    // canonicalizes onto a real plugin reaches `DuplicatePlugin` through the real
    // `discover_under` path (Unix-only fixture; the host is darwin).
    #[cfg(unix)]
    #[test]
    fn duplicate_plugin_name_via_symlink() {
        let root = temp_root("dup");
        make_plugin(&root, "foo");
        // `bar` -> `foo`: canonicalizes to foo, so its manifest name "foo" collides.
        std::os::unix::fs::symlink(root.join("foo"), root.join("bar")).expect("symlink");

        assert!(matches!(
            PluginCatalog::discover_under(&root),
            Err(PluginCatalogError::DuplicatePlugin { .. })
        ));
        let _ = std::fs::remove_dir_all(&root);
    }
}
