//! Markdown configuration parsing helpers (`config/markdown.py`).
//!
//! A `---`-delimited YAML frontmatter split shared by every config-format
//! consumer (skills, context-engine, agent profiles). Owned here per the anchor
//! "upstream owns the shared contract" rule; `eos-skills` calls it rather than
//! re-implementing the split.

use serde_yaml::{Mapping, Value};

/// Split a Markdown document into its YAML frontmatter mapping and body text.
///
/// Faithful port of `config/markdown.py:parse_markdown_frontmatter`:
/// - returns `(empty, full_content)` when the document has no leading
///   `---`-delimited block or the closing `---` is missing;
/// - **swallows** malformed YAML (`serde_yaml` parse error) by returning
///   `(empty, full_content)`, matching Python's `except yaml.YAMLError`;
/// - coerces a non-mapping frontmatter (e.g. a bare scalar or list) to an empty
///   mapping, matching Python's `if not isinstance(frontmatter, dict)`;
/// - otherwise returns the parsed mapping and the trimmed post-frontmatter body.
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
        // `safe_load(...) or {}` for null / non-dict frontmatter.
        Ok(_) => Mapping::new(),
        // `except yaml.YAMLError: return {}, content`.
        Err(_) => return (Mapping::new(), content.to_owned()),
    };
    let body = lines[end + 1..].join("\n").trim().to_owned();
    (frontmatter, body)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn name(mapping: &Mapping) -> Option<&str> {
        mapping.get("name").and_then(Value::as_str)
    }

    #[test]
    fn no_frontmatter_returns_empty_and_full_content() {
        let (fm, body) = parse_markdown_frontmatter("# Heading\n\nbody text");
        assert!(fm.is_empty());
        assert_eq!(body, "# Heading\n\nbody text");
    }

    #[test]
    fn parses_frontmatter_and_strips_body() {
        let (fm, body) = parse_markdown_frontmatter("---\nname: planner\n---\n\nbody\n");
        assert_eq!(name(&fm), Some("planner"));
        assert_eq!(body, "body");
    }

    #[test]
    fn unterminated_frontmatter_returns_empty_and_full_content() {
        let content = "---\nname: planner\nno closing fence\n";
        let (fm, body) = parse_markdown_frontmatter(content);
        assert!(fm.is_empty());
        assert_eq!(body, content);
    }

    #[test]
    fn malformed_yaml_is_swallowed() {
        // A mapping value that is not valid YAML scalar structure.
        let content = "---\nname: [unclosed\n---\nbody\n";
        let (fm, body) = parse_markdown_frontmatter(content);
        assert!(fm.is_empty());
        assert_eq!(body, content);
    }

    #[test]
    fn non_mapping_frontmatter_coerces_to_empty() {
        let (fm, body) = parse_markdown_frontmatter("---\n- a\n- b\n---\nbody\n");
        assert!(fm.is_empty());
        assert_eq!(body, "body");
    }
}
