//! In-namespace read-only tool primitives used by the fresh namespace runner.

use std::fs;
use std::path::{Component, Path, PathBuf};

use eos_protocol::models::{GrepArgs, GrepOutputMode, DEFAULT_GLOB_LIMIT, MAX_FILE_BYTES};
use regex::RegexBuilder;
use serde_json::{json, Value};

use crate::RunnerError;

pub fn glob_tool_result(
    args: &Value,
    workspace_root: &Path,
    mount_s: f64,
    tool_s: f64,
) -> Result<Value, RunnerError> {
    let pattern = string_arg(args, "pattern").trim().to_owned();
    if pattern.is_empty() {
        return Err(RunnerError::InvalidRequest(
            "pattern is required".to_owned(),
        ));
    }
    let root = search_root(args, workspace_root)?;
    let mut matches = Vec::new();
    for path in walk_files_no_follow(&root) {
        let search_rel = display_workspace_path(&path, &root);
        if !has_git_component(&path) && glob_matches(&search_rel, &pattern) {
            matches.push(display_workspace_path(&path, workspace_root));
        }
    }
    matches.sort();
    let truncated = matches.len() > DEFAULT_GLOB_LIMIT;
    let filenames: Vec<String> = matches.into_iter().take(DEFAULT_GLOB_LIMIT).collect();
    Ok(base_result(
        mount_s,
        tool_s,
        json!({
            "filenames": filenames,
            "num_files": usize_to_i64_saturating(filenames.len()),
            "truncated": truncated,
        }),
    ))
}

pub fn grep_tool_result(
    args: &Value,
    workspace_root: &Path,
    mount_s: f64,
    tool_s: f64,
) -> Result<Value, RunnerError> {
    let grep_args: GrepArgs = serde_json::from_value(args.clone())
        .map_err(|err| RunnerError::InvalidRequest(err.to_string()))?;
    if grep_args.pattern.is_empty() {
        return Err(RunnerError::InvalidRequest(
            "pattern is required".to_owned(),
        ));
    }
    let output_mode = grep_args.output_mode;
    let regex = RegexBuilder::new(&grep_args.pattern)
        .multi_line(true)
        .case_insensitive(grep_args.case_insensitive)
        .dot_matches_new_line(grep_args.multiline)
        .build()
        .map_err(|err| RunnerError::InvalidRequest(err.to_string()))?;
    let root = search_root(args, workspace_root)?;
    let glob_filter = grep_args.glob_filter;
    let mut files = candidate_files_no_follow(&root);
    files.sort();

    let mut filenames = Vec::new();
    let mut content_lines = Vec::new();
    let mut num_matches = 0_i64;
    for path in files {
        let rel = display_workspace_path(&path, workspace_root);
        if glob_filter
            .as_deref()
            .is_some_and(|filter| !fnmatch(filter, &rel))
        {
            continue;
        }
        let Some(text) = read_utf8_file_no_follow(&path)? else {
            continue;
        };
        let matches: Vec<_> = regex.find_iter(&text).collect();
        if matches.is_empty() {
            continue;
        }
        filenames.push(rel.clone());
        num_matches = num_matches.saturating_add(usize_to_i64_saturating(matches.len()));
        match output_mode {
            GrepOutputMode::Count => content_lines.push(format!("{}:{}", rel, matches.len())),
            GrepOutputMode::Content => {
                content_lines.extend(matching_lines(&rel, &text, &regex, grep_args.line_numbers));
            }
            GrepOutputMode::FilesWithMatches => {}
        }
    }
    let content = if content_lines.is_empty() {
        String::new()
    } else {
        format!("{}\n", content_lines.join("\n"))
    };
    Ok(base_result(
        mount_s,
        tool_s,
        json!({
            "output_mode": output_mode,
            "filenames": filenames,
            "content": content,
            "num_files": usize_to_i64_saturating(filenames.len()),
            "num_lines": if output_mode == GrepOutputMode::Content { usize_to_i64_saturating(content_lines.len()) } else { 0 },
            "num_matches": num_matches,
            "applied_limit": null,
            "applied_offset": 0,
            "truncated": false,
        }),
    ))
}

fn base_result(mount_s: f64, tool_s: f64, fields: Value) -> Value {
    let mut result = json!({
        "success": true,
        "workspace": "ephemeral",
        "timings": {
            "workspace.mount_s": mount_s,
            "workspace.tool_s": tool_s,
        },
        "conflict": null,
        "conflict_reason": null,
        "changed_paths": [],
        "error": null,
    });
    let Value::Object(extra) = fields else {
        return result;
    };
    let Value::Object(result_obj) = &mut result else {
        return result;
    };
    for (key, value) in extra {
        result_obj.insert(key, value);
    }
    result
}

fn usize_to_i64_saturating(value: usize) -> i64 {
    i64::try_from(value).unwrap_or(i64::MAX)
}

fn search_root(args: &Value, workspace_root: &Path) -> Result<PathBuf, RunnerError> {
    let raw = optional_string_arg(args, "path").unwrap_or_else(|| ".".to_owned());
    let candidate = PathBuf::from(&raw);
    let workspace_root = normalize_lexical(workspace_root);
    let resolved = if candidate.is_absolute() {
        normalize_lexical(&candidate)
    } else {
        normalize_lexical(&workspace_root.join(candidate))
    };
    if !resolved.starts_with(&workspace_root) {
        return Err(RunnerError::InvalidRequest(format!(
            "path escapes workspace replacement root: {raw}"
        )));
    }
    Ok(resolved)
}

fn candidate_files_no_follow(root: &Path) -> Vec<PathBuf> {
    if is_regular_file_no_follow(root) {
        return vec![root.to_path_buf()];
    }
    walk_files_no_follow(root)
}

fn walk_files_no_follow(root: &Path) -> Vec<PathBuf> {
    let mut files = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let Ok(metadata) = fs::symlink_metadata(&dir) else {
            continue;
        };
        if metadata.file_type().is_symlink() || !metadata.is_dir() {
            continue;
        }
        let Ok(entries) = fs::read_dir(&dir) else {
            continue;
        };
        for entry in entries {
            let Ok(entry) = entry else {
                continue;
            };
            let path = entry.path();
            let Ok(file_type) = entry.file_type() else {
                continue;
            };
            if file_type.is_symlink() {
                continue;
            }
            if file_type.is_dir() {
                stack.push(path);
            } else if file_type.is_file() {
                files.push(path);
            }
        }
    }
    files
}

fn read_utf8_file_no_follow(path: &Path) -> Result<Option<String>, RunnerError> {
    let Ok(metadata) = fs::symlink_metadata(path) else {
        return Ok(None);
    };
    if metadata.file_type().is_symlink()
        || !metadata.is_file()
        || metadata.len() > u64::try_from(MAX_FILE_BYTES).unwrap_or(u64::MAX)
    {
        return Ok(None);
    }
    let bytes = fs::read(path).map_err(RunnerError::Child)?;
    Ok(String::from_utf8(bytes).ok())
}

fn is_regular_file_no_follow(path: &Path) -> bool {
    let Ok(metadata) = fs::symlink_metadata(path) else {
        return false;
    };
    !metadata.file_type().is_symlink() && metadata.is_file()
}

fn matching_lines(rel: &str, text: &str, regex: &regex::Regex, line_numbers: bool) -> Vec<String> {
    text.lines()
        .enumerate()
        .filter_map(|(index, line)| {
            if !regex.is_match(line) {
                return None;
            }
            let prefix = if line_numbers {
                format!("{}:{}:", rel, index + 1)
            } else {
                format!("{rel}:")
            };
            Some(format!("{prefix}{line}"))
        })
        .collect()
}

fn glob_matches(rel: &str, pattern: &str) -> bool {
    if !pattern.contains('/') {
        return !rel.contains('/') && fnmatch(pattern, rel);
    }
    if fnmatch(pattern, rel) {
        return true;
    }
    pattern
        .strip_prefix("**/")
        .is_some_and(|stripped| fnmatch(stripped, rel))
}

fn fnmatch(pattern: &str, value: &str) -> bool {
    wildcard_match(pattern.as_bytes(), value.as_bytes())
}

fn wildcard_match(pattern: &[u8], value: &[u8]) -> bool {
    let (mut p, mut v) = (0, 0);
    let mut star = None;
    let mut star_value = 0;
    while v < value.len() {
        if p < pattern.len() && (pattern[p] == b'?' || pattern[p] == value[v]) {
            p += 1;
            v += 1;
        } else if p < pattern.len() && pattern[p] == b'*' {
            star = Some(p);
            p += 1;
            star_value = v;
        } else if let Some(star_index) = star {
            p = star_index + 1;
            star_value += 1;
            v = star_value;
        } else {
            return false;
        }
    }
    while p < pattern.len() && pattern[p] == b'*' {
        p += 1;
    }
    p == pattern.len()
}

fn string_arg(args: &Value, key: &str) -> String {
    args.get(key)
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned()
}

fn optional_string_arg(args: &Value, key: &str) -> Option<String> {
    args.get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
}

fn display_workspace_path(path: &Path, workspace_root: &Path) -> String {
    path.strip_prefix(workspace_root)
        .unwrap_or(path)
        .to_string_lossy()
        .replace('\\', "/")
}

fn has_git_component(path: &Path) -> bool {
    path.components()
        .any(|component| component.as_os_str() == ".git")
}

fn normalize_lexical(path: &Path) -> PathBuf {
    let mut normalized = PathBuf::new();
    for component in path.components() {
        match component {
            Component::CurDir => {}
            Component::ParentDir => {
                normalized.pop();
            }
            other => normalized.push(other.as_os_str()),
        }
    }
    normalized
}

#[cfg(test)]
mod tests {
    use std::sync::atomic::{AtomicU64, Ordering};

    use super::*;

    #[test]
    fn glob_matches_root_files_and_skips_nested_for_basename_pattern(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let fixture = Fixture::new("glob")?;
        fixture.write("a.py", "hit")?;
        fixture.write("pkg/b.py", "hit")?;
        fixture.write(".git/config", "secret")?;

        let result = glob_tool_result(&json!({"pattern": "*.py"}), &fixture.root, 0.1, 0.2)?;

        assert_eq!(result["success"], json!(true));
        assert_eq!(result["filenames"], json!(["a.py"]));
        assert_eq!(result["num_files"], json!(1));
        Ok(())
    }

    #[test]
    fn glob_matches_basename_pattern_relative_to_search_root(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let fixture = Fixture::new("glob-subdir")?;
        fixture.write("pkg/app.py", "hit")?;
        fixture.write("pkg/nested/skip.py", "hit")?;

        let result = glob_tool_result(
            &json!({"pattern": "*.py", "path": "pkg"}),
            &fixture.root,
            0.1,
            0.2,
        )?;

        assert_eq!(result["filenames"], json!(["pkg/app.py"]));
        assert_eq!(result["num_files"], json!(1));
        Ok(())
    }

    #[test]
    fn grep_content_counts_and_line_numbers_match_wire_contract(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let fixture = Fixture::new("grep")?;
        fixture.write("a.py", "one\nHit\n")?;
        fixture.write("pkg/b.txt", "hit\nhit\n")?;

        let result = grep_tool_result(
            &json!({
                "pattern": "hit",
                "output_mode": "content",
                "case_insensitive": true,
                "line_numbers": true,
                "path": ".",
                "glob_filter": "*.py"
            }),
            &fixture.root,
            0.1,
            0.2,
        )?;

        assert_eq!(result["filenames"], json!(["a.py"]));
        assert_eq!(result["content"], json!("a.py:2:Hit\n"));
        assert_eq!(result["num_lines"], json!(1));
        assert_eq!(result["num_matches"], json!(1));
        assert_eq!(result["applied_limit"], Value::Null);
        Ok(())
    }

    #[test]
    fn grep_rejects_unknown_output_mode() -> Result<(), Box<dyn std::error::Error>> {
        let fixture = Fixture::new("grep-mode")?;
        fixture.write("a.py", "hit\n")?;

        let Err(err) = grep_tool_result(
            &json!({"pattern": "hit", "output_mode": "bogus"}),
            &fixture.root,
            0.0,
            0.0,
        ) else {
            return Err("unknown output_mode should be rejected".into());
        };

        assert!(err.to_string().contains("invalid namespace runner request"));
        Ok(())
    }

    #[test]
    fn search_root_rejects_parent_escape() -> Result<(), Box<dyn std::error::Error>> {
        let fixture = Fixture::new("escape")?;
        let Err(err) = grep_tool_result(
            &json!({"pattern": "x", "output_mode": "count", "path": "../outside"}),
            &fixture.root,
            0.0,
            0.0,
        ) else {
            return Err("parent escape should be rejected".into());
        };
        assert!(err.to_string().contains("invalid namespace runner request"));
        Ok(())
    }

    struct Fixture {
        root: PathBuf,
    }

    impl Fixture {
        fn new(label: &str) -> Result<Self, std::io::Error> {
            static COUNTER: AtomicU64 = AtomicU64::new(0);
            let root = std::env::temp_dir().join(format!(
                "eos-runner-{label}-{}-{}",
                std::process::id(),
                COUNTER.fetch_add(1, Ordering::Relaxed)
            ));
            let _ = fs::remove_dir_all(&root);
            fs::create_dir_all(&root)?;
            Ok(Self { root })
        }

        fn write(&self, path: &str, content: &str) -> Result<(), std::io::Error> {
            let path = self.root.join(path);
            let parent = path.parent().ok_or_else(|| {
                std::io::Error::new(
                    std::io::ErrorKind::InvalidInput,
                    "fixture path has no parent",
                )
            })?;
            fs::create_dir_all(parent)?;
            fs::write(path, content)
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.root);
        }
    }
}
