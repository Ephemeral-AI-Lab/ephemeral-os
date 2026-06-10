//! Shared unit-test fixture: a seeded on-disk layer stack under a tempdir.

use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

use serde_json::json;

use crate::LayerStack;

pub(crate) type TestResult<T = ()> = Result<T, Box<dyn std::error::Error + Send + Sync>>;

pub(crate) fn unique_suffix() -> String {
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    format!(
        "{}-{}",
        std::process::id(),
        COUNTER.fetch_add(1, Ordering::Relaxed)
    )
}

pub(crate) fn lp(path: &str) -> TestResult<eos_cas::LayerPath> {
    Ok(eos_cas::LayerPath::parse(path)?)
}

pub(crate) struct Fixture {
    pub(crate) base: PathBuf,
    pub(crate) root: PathBuf,
}

impl Fixture {
    pub(crate) fn new(label: &str) -> TestResult<Self> {
        Self::new_with_gitignores(label, &[])
    }

    pub(crate) fn new_with_gitignore(label: &str, gitignore: &str) -> TestResult<Self> {
        let seeds = if gitignore.is_empty() {
            Vec::new()
        } else {
            vec![("", gitignore)]
        };
        Self::new_with_gitignores(label, &seeds)
    }

    /// Seed one base layer with a `.gitignore` per `(dir, contents)` entry
    /// (`""` = workspace root) so nested / depth-sensitive routing is testable.
    pub(crate) fn new_with_gitignores(
        label: &str,
        gitignores: &[(&str, &str)],
    ) -> TestResult<Self> {
        let base = std::env::temp_dir().join(format!("eos-layerstack-{label}-{}", unique_suffix()));
        let _ = std::fs::remove_dir_all(&base);
        let root = base.join("layer-stack");
        let layer = root.join("layers").join("B000001-base");
        std::fs::create_dir_all(&layer)?;
        std::fs::create_dir_all(root.join("staging"))?;
        std::fs::write(layer.join("README.md"), "# README\n")?;
        for (dir, contents) in gitignores {
            let target = if dir.is_empty() {
                layer.join(".gitignore")
            } else {
                layer.join(dir).join(".gitignore")
            };
            if let Some(parent) = target.parent() {
                std::fs::create_dir_all(parent)?;
            }
            std::fs::write(target, contents)?;
        }
        std::fs::write(
            root.join("manifest.json"),
            serde_json::to_string_pretty(&json!({
                "schema_version": 1,
                "version": 1,
                "layers": [{"layer_id": "B000001-base", "path": "layers/B000001-base"}],
            }))?,
        )?;
        Ok(Self { base, root })
    }

    pub(crate) fn read_text(&self, path: &str) -> TestResult<String> {
        Ok(LayerStack::open(self.root.clone())?.read_text(path)?.0)
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.base);
    }
}
