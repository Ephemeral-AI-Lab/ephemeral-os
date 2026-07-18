use std::path::{Path, PathBuf};

use ignore::gitignore::{Gitignore, GitignoreBuilder};

use crate::error::LayerStackError;
use crate::model::{LayerPath, Manifest};
use crate::stack::projection::MergedEntry;
use crate::stack::MergedView;

pub(crate) struct GitignoreOracle<'a> {
    view: &'a MergedView,
    manifest: &'a Manifest,
}

impl<'a> GitignoreOracle<'a> {
    pub(crate) const fn new(view: &'a MergedView, manifest: &'a Manifest) -> Self {
        Self { view, manifest }
    }

    pub(crate) fn is_ignored(
        &self,
        path: &LayerPath,
        is_dir: bool,
    ) -> Result<bool, LayerStackError> {
        let components = path.as_str().split('/').collect::<Vec<_>>();
        let mut ignored = false;
        for depth in 0..components.len() {
            let dir = components[..depth].join("/");
            let matcher = self.matcher_for_dir(&dir)?;
            let relative = components[depth..].join("/");
            if relative.is_empty() {
                continue;
            }
            if is_sealed_by_parent(&matcher, Path::new(&relative)) {
                return Ok(true);
            }
            let matched = matcher.matched(Path::new(&relative), is_dir);
            if matched.is_ignore() {
                ignored = true;
            } else if matched.is_whitelist() {
                ignored = false;
            }
        }
        Ok(ignored)
    }

    fn matcher_for_dir(&self, dir: &str) -> Result<Gitignore, LayerStackError> {
        let gitignore_path = if dir.is_empty() {
            ".gitignore".to_owned()
        } else {
            format!("{dir}/.gitignore")
        };
        let entry = self.view.read_entry(&gitignore_path, self.manifest)?;
        let MergedEntry::File { bytes, .. } = entry else {
            return GitignoreBuilder::new(".")
                .build()
                .map_err(|err| LayerStackError::Storage(err.to_string()));
        };
        let Ok(contents) = String::from_utf8(bytes) else {
            return GitignoreBuilder::new(".")
                .build()
                .map_err(|err| LayerStackError::Storage(err.to_string()));
        };
        let mut builder = GitignoreBuilder::new(".");
        let from = Some(PathBuf::from(gitignore_path));
        for line in contents.lines() {
            let _ = builder.add_line(from.clone(), line);
        }
        builder
            .build()
            .map_err(|err| LayerStackError::Storage(err.to_string()))
    }
}

fn is_sealed_by_parent(matcher: &Gitignore, relative: &Path) -> bool {
    let mut parent = relative.parent();
    while let Some(path) = parent {
        if path.as_os_str().is_empty() {
            break;
        }
        if matcher.matched(path, true).is_ignore() {
            return true;
        }
        parent = path.parent();
    }
    false
}
