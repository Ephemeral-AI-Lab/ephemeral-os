use ignore::gitignore::GitignoreBuilder;
use ignore::Match;

use crate::{LayerStack, LayerStackError, Manifest, MergedView};

pub(crate) trait IgnoreSource {
    fn read_bytes(&self, path: &str) -> Result<(Option<Vec<u8>>, bool), LayerStackError>;
}

impl IgnoreSource for LayerStack {
    fn read_bytes(&self, path: &str) -> Result<(Option<Vec<u8>>, bool), LayerStackError> {
        Self::read_bytes(self, path)
    }
}

pub(crate) struct ManifestIgnoreSource<'a> {
    pub(crate) view: &'a MergedView,
    pub(crate) manifest: &'a Manifest,
}

impl IgnoreSource for ManifestIgnoreSource<'_> {
    fn read_bytes(&self, path: &str) -> Result<(Option<Vec<u8>>, bool), LayerStackError> {
        self.view.read_bytes(path, self.manifest)
    }
}

pub(super) fn path_is_ignored(
    source: &impl IgnoreSource,
    path: &str,
) -> Result<bool, LayerStackError> {
    let rel = path.trim_start_matches('/');
    if rel.is_empty() {
        return Ok(false);
    }
    let parts: Vec<&str> = rel.split('/').collect();
    let mut accum = String::new();
    for part in &parts[..parts.len() - 1] {
        accum = join_rel(&accum, part);
        if dir_is_excluded(source, &accum)? {
            return Ok(true);
        }
    }
    match_with_inheritance(source, rel, false)
}

fn dir_is_excluded(source: &impl IgnoreSource, dir_rel: &str) -> Result<bool, LayerStackError> {
    let mut accum = String::new();
    let mut excluded = false;
    for part in dir_rel.split('/').filter(|part| !part.is_empty()) {
        accum = join_rel(&accum, part);
        if !excluded {
            excluded = match_with_inheritance(source, &accum, true)?;
        }
    }
    Ok(excluded)
}

fn match_with_inheritance(
    source: &impl IgnoreSource,
    path: &str,
    as_dir: bool,
) -> Result<bool, LayerStackError> {
    let parts: Vec<&str> = path.split('/').collect();
    let mut ignored = false;
    let mut accum = String::new();
    for part in &parts {
        if let Some(matcher) = matcher_for(source, &accum)? {
            let sub = if accum.is_empty() {
                path
            } else {
                path[accum.len()..].trim_start_matches('/')
            };
            if !sub.is_empty() {
                match matcher.matched(sub, as_dir) {
                    Match::Ignore(_) => ignored = true,
                    Match::Whitelist(_) => ignored = false,
                    Match::None => {}
                }
            }
        }
        accum = join_rel(&accum, part);
    }
    Ok(ignored)
}

fn matcher_for(
    source: &impl IgnoreSource,
    dir_rel: &str,
) -> Result<Option<ignore::gitignore::Gitignore>, LayerStackError> {
    let rel = join_rel(dir_rel, ".gitignore");
    let (bytes, exists) = source.read_bytes(&rel)?;
    if !exists {
        return Ok(None);
    }
    let Some(bytes) = bytes else {
        return Ok(None);
    };
    let Ok(text) = String::from_utf8(bytes) else {
        return Ok(None);
    };
    let mut builder = GitignoreBuilder::new(".");
    for line in text.lines() {
        let _ = builder.add_line(None, line);
    }
    Ok(builder.build().ok())
}

fn join_rel(prefix: &str, child: &str) -> String {
    if prefix.is_empty() {
        child.to_owned()
    } else {
        format!("{prefix}/{child}")
    }
}
