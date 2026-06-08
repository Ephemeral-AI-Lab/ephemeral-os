//! Directory-based skill loading (`skills/bundled/__init__.py`).
//!
//! Each skill is a subdirectory of the configured root containing a `SKILL.md`
//! (with optional `---` YAML frontmatter) and an optional `references/` directory
//! of `*.md` files. The `---` split itself is **not** re-implemented here: it is
//! the shared [`eos_types::parse_markdown_frontmatter`] helper. Only the
//! skills-specific `name`/`description` fallback lives in this module.

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

use eos_types::parse_markdown_frontmatter;
use serde_yaml::Value;

use crate::definition::{ReferenceName, SkillDefinition, SkillName, SkillSource};
use crate::error::SkillLoadError;

const SKILL_FILE: &str = "SKILL.md";
const REFERENCES_DIR: &str = "references";
const DESCRIPTION_MAX_CHARS: usize = 200;

/// Load every directory skill under `content_dir` in sorted order.
///
/// The caller ([`crate::loader`]) guarantees `content_dir` is an existing
/// directory; here it is walked one level deep, requiring `SKILL.md` per skill.
pub(crate) fn load_bundled_skills(
    content_dir: &Path,
) -> Result<Vec<SkillDefinition>, SkillLoadError> {
    let mut skills = Vec::new();
    for skill_dir in read_dir_sorted(content_dir)? {
        if !skill_dir.is_dir() {
            continue;
        }
        let skill_md = skill_dir.join(SKILL_FILE);
        if !skill_md.is_file() {
            continue;
        }
        let content = read_file(&skill_md)?;
        let (name, description) = parse_skill_metadata(&dir_name(&skill_dir), &content);
        let references = discover_references(&skill_dir)?;
        skills.push(SkillDefinition {
            name: SkillName::parse(name)?,
            description,
            content,
            source: SkillSource::Bundled,
            path: Some(skill_dir),
            references,
        });
    }
    Ok(skills)
}

/// Read `references/*.md` keyed by file stem (`__init__.py` lines 29-34).
fn discover_references(
    skill_dir: &Path,
) -> Result<BTreeMap<ReferenceName, String>, SkillLoadError> {
    let mut references = BTreeMap::new();
    let refs_dir = skill_dir.join(REFERENCES_DIR);
    if !refs_dir.is_dir() {
        return Ok(references);
    }
    for ref_file in read_dir_sorted(&refs_dir)? {
        if !ref_file.is_file() || extension(&ref_file) != Some("md") {
            continue;
        }
        let Some(stem) = ref_file.file_stem().and_then(|s| s.to_str()) else {
            continue;
        };
        let content = read_file(&ref_file)?;
        references.insert(ReferenceName::parse(stem)?, content);
    }
    Ok(references)
}

/// Extract `(name, description)` from a skill markdown file (`__init__.py` 49-67).
///
/// Frontmatter `name`/`description` win; otherwise scan the **full content**
/// (not the post-frontmatter body) for a `# ` heading (used as `name` iff `name`
/// is still the default) and the first non-blank line that starts with neither
/// `#` nor `---`, truncated to [`DESCRIPTION_MAX_CHARS`]; the final fallback is
/// `"Bundled skill: {name}"`.
fn parse_skill_metadata(default_name: &str, content: &str) -> (String, String) {
    let (frontmatter, _body) = parse_markdown_frontmatter(content);
    let mut name = frontmatter_str(&frontmatter, "name")
        .unwrap_or(default_name)
        .to_owned();
    let mut description = frontmatter_str(&frontmatter, "description")
        .unwrap_or_default()
        .to_owned();

    if description.is_empty() {
        for line in content.lines() {
            let stripped = line.trim();
            if let Some(heading) = stripped.strip_prefix("# ") {
                if name.is_empty() || name == default_name {
                    let heading = heading.trim();
                    name = if heading.is_empty() {
                        default_name.to_owned()
                    } else {
                        heading.to_owned()
                    };
                }
                continue;
            }
            if !stripped.is_empty() && !stripped.starts_with("---") && !stripped.starts_with('#') {
                description = stripped.chars().take(DESCRIPTION_MAX_CHARS).collect();
                break;
            }
        }
    }

    let description = if description.is_empty() {
        format!("Bundled skill: {name}")
    } else {
        description
    };
    (name, description)
}

/// A frontmatter string field, treating empty as absent (Rust's `... or`).
fn frontmatter_str<'a>(frontmatter: &'a serde_yaml::Mapping, key: &str) -> Option<&'a str> {
    frontmatter
        .get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
}

fn dir_name(path: &Path) -> String {
    path.file_name()
        .and_then(|n| n.to_str())
        .unwrap_or_default()
        .to_owned()
}

fn extension(path: &Path) -> Option<&str> {
    path.extension().and_then(|e| e.to_str())
}

/// List a directory's entries as paths, sorted (parity with Rust `sorted(...)`).
fn read_dir_sorted(dir: &Path) -> Result<Vec<PathBuf>, SkillLoadError> {
    let mut paths = Vec::new();
    let entries = fs::read_dir(dir).map_err(|cause| SkillLoadError::ReadDir {
        path: dir.to_owned(),
        cause,
    })?;
    for entry in entries {
        let entry = entry.map_err(|cause| SkillLoadError::ReadDir {
            path: dir.to_owned(),
            cause,
        })?;
        paths.push(entry.path());
    }
    paths.sort();
    Ok(paths)
}

fn read_file(path: &Path) -> Result<String, SkillLoadError> {
    fs::read_to_string(path).map_err(|cause| SkillLoadError::ReadFile {
        path: path.to_owned(),
        cause,
    })
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // unwrap is permitted in tests (err-no-unwrap-prod)
    use super::*;
    use crate::support::Scratch;

    // AC-skills-02: only *.md references are kept, keyed by stem, in sorted order.
    #[test]
    fn discovers_only_md_references_keyed_by_stem() {
        let scratch = Scratch::new("refs-by-stem");
        scratch.write("skill/SKILL.md", "---\nname: skill\ndescription: d\n---\n");
        scratch.write("skill/references/rubric.md", "RUBRIC");
        scratch.write("skill/references/checklist.md", "CHECKLIST");
        scratch.write("skill/references/.gitkeep", "");
        scratch.write("skill/references/notes.txt", "NOTES");

        let skills = load_bundled_skills(scratch.path()).unwrap();
        assert_eq!(skills.len(), 1);
        let references = &skills[0].references;

        let keys: Vec<&str> = references.keys().map(ReferenceName::as_str).collect();
        assert_eq!(keys, vec!["checklist", "rubric"]);
        assert_eq!(
            references
                .get(&ReferenceName::parse("checklist").unwrap())
                .map(String::as_str),
            Some("CHECKLIST")
        );
    }

    // AC-skills-09: malformed frontmatter is swallowed; the load still succeeds
    // and the description comes from the full-content fallback scan.
    #[test]
    fn malformed_frontmatter_uses_fallback_description() {
        let scratch = Scratch::new("malformed-fm");
        // An unterminated quoted scalar is invalid YAML -> swallowed -> empty.
        scratch.write("broken/SKILL.md", "---\nname: \"unterminated\n---\nbody\n");

        let result = load_bundled_skills(scratch.path());
        assert!(
            result.is_ok(),
            "malformed frontmatter must not fail the load"
        );
        let skills = result.unwrap();
        assert_eq!(skills.len(), 1);
        // Frontmatter swallowed -> name defaults to the directory name.
        assert_eq!(skills[0].name.as_str(), "broken");
        // Description = first non-blank line that is not `#`/`---` (the fm line).
        assert_eq!(skills[0].description, "name: \"unterminated");
    }

    // AC-skills-10: name present + description absent + no heading -> the
    // description is the first frontmatter key line of the full content.
    #[test]
    fn description_falls_back_to_full_content_lines() {
        let scratch = Scratch::new("fullscan-desc");
        scratch.write("planner/SKILL.md", "---\nname: planner\n---\n");

        let skills = load_bundled_skills(scratch.path()).unwrap();
        assert_eq!(skills[0].name.as_str(), "planner");
        assert_eq!(skills[0].description, "name: planner");
    }

    // AC-skills-11: a dotted reference stem round-trips as its key.
    #[test]
    fn dotted_reference_stem_is_keyed() {
        let scratch = Scratch::new("dotted-stem");
        scratch.write("skill/SKILL.md", "---\nname: skill\ndescription: d\n---\n");
        scratch.write("skill/references/api.v2.md", "V2 CONTENT");

        let skills = load_bundled_skills(scratch.path()).unwrap();
        assert_eq!(
            skills[0]
                .references
                .get(&ReferenceName::parse("api.v2").unwrap())
                .map(String::as_str),
            Some("V2 CONTENT")
        );
    }

    // A present-but-empty frontmatter `name` falls back to the directory name
    // (Rust `str(frontmatter.get("name") or default_name)`). This guards the
    // load-bearing `frontmatter_str` empty-string filter: without it,
    // `SkillName::parse("")` would abort the whole load with `InvalidName`.
    #[test]
    fn empty_frontmatter_name_falls_back_to_dir_name() {
        let scratch = Scratch::new("empty-name");
        scratch.write("skill/SKILL.md", "---\nname: \"\"\ndescription: d\n---\n");

        let skills = load_bundled_skills(scratch.path()).unwrap();
        assert_eq!(skills.len(), 1);
        assert_eq!(skills[0].name.as_str(), "skill");
        assert_eq!(skills[0].description, "d");
    }

    // A subdirectory lacking `SKILL.md` is skipped without error (spec §8.1;
    // Rust `if skill_md.exists()`), leaving sibling skills intact.
    #[test]
    fn skill_dir_without_skill_md_is_skipped_without_error() {
        let scratch = Scratch::new("missing-skill-md");
        scratch.write("good/SKILL.md", "---\nname: good\ndescription: d\n---\n");
        scratch.write("half_built/notes.txt", "WIP"); // dir exists, no SKILL.md

        let skills = load_bundled_skills(scratch.path()).unwrap();
        assert_eq!(skills.len(), 1);
        assert_eq!(skills[0].name.as_str(), "good");
    }
}
