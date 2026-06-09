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
