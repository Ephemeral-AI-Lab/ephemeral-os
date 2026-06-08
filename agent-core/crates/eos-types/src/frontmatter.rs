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
mod tests {
    use super::*;

    fn name(mapping: &Mapping) -> Option<&str> {
        mapping.get("name").and_then(Value::as_str)
    }

    #[test]
    fn no_frontmatter_returns_empty_and_full_content() {
        let (frontmatter, body) = parse_markdown_frontmatter("# Heading\n\nbody text");
        assert!(frontmatter.is_empty());
        assert_eq!(body, "# Heading\n\nbody text");
    }

    #[test]
    fn parses_frontmatter_and_strips_body() {
        let (frontmatter, body) = parse_markdown_frontmatter("---\nname: planner\n---\n\nbody\n");
        assert_eq!(name(&frontmatter), Some("planner"));
        assert_eq!(body, "body");
    }

    #[test]
    fn unterminated_frontmatter_returns_empty_and_full_content() {
        let content = "---\nname: planner\nno closing fence\n";
        let (frontmatter, body) = parse_markdown_frontmatter(content);
        assert!(frontmatter.is_empty());
        assert_eq!(body, content);
    }

    #[test]
    fn malformed_yaml_is_swallowed() {
        let content = "---\nname: [unclosed\n---\nbody\n";
        let (frontmatter, body) = parse_markdown_frontmatter(content);
        assert!(frontmatter.is_empty());
        assert_eq!(body, content);
    }

    #[test]
    fn non_mapping_frontmatter_coerces_to_empty() {
        let (frontmatter, body) = parse_markdown_frontmatter("---\n- a\n- b\n---\nbody\n");
        assert!(frontmatter.is_empty());
        assert_eq!(body, "body");
    }
}
