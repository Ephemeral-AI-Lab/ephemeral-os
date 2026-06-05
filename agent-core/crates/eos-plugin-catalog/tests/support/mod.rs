//! Shared `#[cfg(test)]` fixtures for the manifest and discovery test modules.
//!
//! One process-global atomic counter plus the pid and a per-call tag keep every
//! [`temp_root`] unique across both modules. [`make_plugin`] takes an optional
//! manifest override: `None` reproduces discovery's minimal one-tool plugin
//! (manifest + `tools/x.py` stub); `Some(md)` writes the given manifest body and
//! the explicit `files`.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

/// A fresh, empty catalog-root temp dir, unique per call.
pub(crate) fn temp_root(tag: &str) -> PathBuf {
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let n = COUNTER.fetch_add(1, Ordering::Relaxed);
    let dir = std::env::temp_dir().join(format!(
        "eos_plugin_catalog_{}_{tag}_{n}",
        std::process::id()
    ));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).expect("create temp root");
    dir
}

/// Create `<root>/<name>/` with a `plugin.md` and its declared files.
///
/// `manifest = None` writes discovery's minimal one-tool manifest plus the
/// `tools/x.py` stub it references; `Some(md)` writes `md` and only the explicit
/// `files`.
pub(crate) fn make_plugin(
    root: &Path,
    name: &str,
    manifest: Option<&str>,
    files: &[&str],
) -> PathBuf {
    let dir = root.join(name);
    std::fs::create_dir_all(&dir).expect("create plugin dir");
    let (manifest_md, files) = match manifest {
        Some(md) => (md.to_owned(), files),
        None => (
            format!(
                "---\nname: {name}\ndescription: d\ntools:\n  - name: {name}.x\n    module: tools/x.py\n---\n"
            ),
            DEFAULT_FILES,
        ),
    };
    std::fs::write(dir.join("plugin.md"), manifest_md).expect("write plugin.md");
    for f in files {
        let path = dir.join(f);
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).expect("create parent");
        }
        std::fs::write(&path, b"# stub\n").expect("write stub file");
    }
    dir
}

/// The default module file referenced by the `None` manifest.
const DEFAULT_FILES: &[&str] = &["tools/x.py"];
