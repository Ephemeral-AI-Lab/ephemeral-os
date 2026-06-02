//! `pub(crate)` `---`-delimited YAML frontmatter split.
//!
//! Ports `plugins/core/manifest.py`'s `_FRONTMATTER_RE` without a `regex`
//! dependency: a line-based scan equivalent to the anchored
//! `\A---\s*\n(frontmatter)\n---\s*(?:\n(body))?\Z` pattern for the manifests
//! this crate parses. This split is also needed by `eos-skills`; per the §3 DRY
//! note it is duplicated crate-locally until a third consumer appears.

/// Split `plugin.md` text into `(frontmatter_yaml, body)`.
///
/// Returns `None` when the text does not begin with a `---` line or has no
/// closing `---` line. The `body` is returned untrimmed; the caller trims it.
pub(crate) fn split_frontmatter(text: &str) -> Option<(String, String)> {
    let mut lines = text.lines();
    // The text must begin with a `---` line (trailing whitespace allowed, no
    // leading whitespace — matching the regex `\A---\s*`).
    if lines.next()?.trim_end() != "---" {
        return None;
    }

    let mut frontmatter: Vec<&str> = Vec::new();
    let mut body: Vec<&str> = Vec::new();
    let mut closed = false;
    for line in lines {
        if !closed && line.trim_end() == "---" {
            closed = true;
            continue;
        }
        if closed {
            body.push(line);
        } else {
            frontmatter.push(line);
        }
    }
    if !closed {
        return None;
    }
    Some((frontmatter.join("\n"), body.join("\n")))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn splits_frontmatter_and_body() {
        let text = "---\nname: lsp\n---\n\n# Heading\nbody text\n";
        let (front, body) = split_frontmatter(text).expect("splits");
        assert_eq!(front, "name: lsp");
        assert_eq!(body.trim(), "# Heading\nbody text");
    }

    #[test]
    fn missing_or_unterminated_frontmatter_is_none() {
        assert!(split_frontmatter("no frontmatter here").is_none());
        assert!(split_frontmatter("---\nname: lsp\nno close").is_none());
        // Leading whitespace before the opening fence does not match.
        assert!(split_frontmatter("  ---\nname: lsp\n---\n").is_none());
    }

    #[test]
    fn empty_frontmatter_yields_empty_string() {
        let (front, body) = split_frontmatter("---\n---\n").expect("splits");
        assert_eq!(front, "");
        assert_eq!(body, "");
    }
}
