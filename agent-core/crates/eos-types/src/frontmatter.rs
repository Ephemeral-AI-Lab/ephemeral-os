//! Pure Markdown frontmatter splitting.

use serde_yaml::{Mapping, Value};

/// Split a Markdown document into its YAML frontmatter mapping and body text.
///
/// Returns `(empty, full_content)` when the document has no leading
/// `---`-delimited block or the closing delimiter is missing. Malformed YAML is
/// swallowed the same way for compatibility with the legacy loader.
#[must_use]
pub fn parse_markdown_frontmatter(content: &str) -> (Mapping, String) {
    let lines: Vec<&str> = content.lines().collect();
    if lines.first().map(|line| line.trim()) != Some("---") {
        return (Mapping::new(), content.to_owned());
    }
    let Some(end) = lines
        .iter()
        .enumerate()
        .skip(1)
        .find(|(_, line)| line.trim() == "---")
        .map(|(index, _)| index)
    else {
        return (Mapping::new(), content.to_owned());
    };
    let block = lines[1..end].join("\n");
    let frontmatter = match serde_yaml::from_str::<Value>(&block) {
        Ok(Value::Mapping(mapping)) => mapping,
        Ok(_) => Mapping::new(),
        Err(_) => return (Mapping::new(), content.to_owned()),
    };
    let body = lines[end + 1..].join("\n").trim().to_owned();
    (frontmatter, body)
}

#[cfg(test)]
#[path = "../tests/frontmatter/mod.rs"]
mod tests;
