//! Test-only filesystem scratch helper, shared by the `bundled` and `loader`
//! test modules. Compiled only under `cfg(test)`.

#![allow(clippy::unwrap_used)] // unwrap is permitted in tests (err-no-unwrap-prod)

use std::fs;
use std::path::{Path, PathBuf};

/// A throwaway directory under the system temp dir, unique per test name and
/// recreated empty. Removed on drop.
pub(crate) struct Scratch(PathBuf);

impl Scratch {
    pub(crate) fn new(name: &str) -> Self {
        let dir = std::env::temp_dir().join(format!("eos-tool-skills-{name}"));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        Self(dir)
    }

    pub(crate) fn path(&self) -> &Path {
        &self.0
    }

    /// Write `body` to `rel` under the scratch dir, creating parents; returns the
    /// absolute path written.
    pub(crate) fn write(&self, rel: &str, body: &str) -> PathBuf {
        let path = self.0.join(rel);
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).unwrap();
        }
        fs::write(&path, body).unwrap();
        path
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}
