//! `pub(crate)` `---`-delimited YAML frontmatter split.
//!
//! Ports `plugins/core/manifest.py`'s `_FRONTMATTER_RE` without a `regex`
//! dependency: a line-based scan equivalent to the anchored
//! `\A---\s*\n(frontmatter)\n---\s*(?:\n(body))?\Z` pattern for the manifests
//! this crate parses.
//!
//! This split is deliberately kept crate-local rather than shared with
//! `eos_config::parse_markdown_frontmatter`. The contracts differ on purpose:
//! this port anchors the opening fence with `trim_end` (a leading-indented
//! `---` does NOT match, matching the regex), returns the *raw* frontmatter
//! string, and returns `None` on a missing/unterminated block so the caller can
//! raise a hard error. The `eos-config` helper instead trims both fences,
//! returns a parsed `serde_yaml::Mapping`, and *swallows* a missing/malformed
//! block into an empty mapping. `eos-skills` reuses the `eos-config` helper and
//! `eos-agent-def` keeps its own `trim`-based raw variant, so consolidating the
//! three would change parse/error behavior (and force an `eos-config` edge onto
//! the deliberately dependency-free `eos-agent-def`).

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
