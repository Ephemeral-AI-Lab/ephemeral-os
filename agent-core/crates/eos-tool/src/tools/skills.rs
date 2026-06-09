//! Skill-reference tools.

#[cfg(test)]
#[path = "../../tests/skills/support/mod.rs"]
mod support;

mod definition {
    //! The skill value type and its validated newtypes.
    //!
    //! A [`SkillDefinition`] is a faithful, immutable port of the Rust
    //! `@DTO(frozen=True)` (`skills/core/types.py`): the same six fields, with
    //! the stringly `source` lifted to a [`SkillSource`] enum and the name / reference
    //! keys lifted to validated newtypes ([`SkillName`], [`ReferenceName`]).

    use std::collections::BTreeMap;
    use std::path::PathBuf;

    use serde::Serialize;

    use super::error::SkillLoadError;

    /// Reject empty names and names carrying a path component — a parent-dir `..`,
    /// a separator (`/` or `\`), or a NUL. A bare `.` and dotted stems (`api.v2`)
    /// are allowed so `ReferenceName`s derived from a `.md` stem round-trip.
    fn validate_name(value: &str) -> bool {
        !value.is_empty() && value != ".." && !value.contains(['/', '\\', '\0'])
    }

    /// A skill name: the parsed name (frontmatter `name`, else the directory name)
    /// and the registry key, a 1:1 port of Rust `registry.py`'s `skill.name` key.
    ///
    /// The traversal-safety guarantee is that names are used **only as map keys**
    /// (never path-joined); this newtype's separator/`..`/NUL rejection is
    /// defense-in-depth on top of that (`api-parse-dont-validate`).
    #[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize)]
    #[serde(transparent)]
    pub struct SkillName(String);

    impl SkillName {
        /// Parse a skill name, rejecting empty or path-bearing values.
        ///
        /// # Errors
        /// Returns [`SkillLoadError::InvalidName`] when the value is empty or
        /// contains a path separator, a `..` component, or a NUL.
        pub fn parse(value: impl Into<String>) -> Result<Self, SkillLoadError> {
            let value = value.into();
            if validate_name(&value) {
                Ok(Self(value))
            } else {
                Err(SkillLoadError::InvalidName(value))
            }
        }

        /// Borrow the name as a string slice.
        #[must_use]
        pub fn as_str(&self) -> &str {
            &self.0
        }
    }

    /// A reference name: a skill's `references/*.md` file **stem** and its map key.
    ///
    /// Same shape and validation as [`SkillName`]; accepts dotted stems like
    /// `api.v2` (matching Rust `ref_file.stem`).
    #[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize)]
    #[serde(transparent)]
    pub struct ReferenceName(String);

    impl ReferenceName {
        /// Parse a reference name, rejecting empty or path-bearing values.
        ///
        /// # Errors
        /// Returns [`SkillLoadError::InvalidName`] when the value is empty or
        /// contains a path separator, a `..` component, or a NUL.
        pub fn parse(value: impl Into<String>) -> Result<Self, SkillLoadError> {
            let value = value.into();
            if validate_name(&value) {
                Ok(Self(value))
            } else {
                Err(SkillLoadError::InvalidName(value))
            }
        }

        /// Borrow the name as a string slice.
        #[must_use]
        pub fn as_str(&self) -> &str {
            &self.0
        }
    }

    /// Where a skill was loaded from. Replaces the Rust free `source: str`.
    #[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
    #[serde(rename_all = "snake_case")]
    #[non_exhaustive]
    pub enum SkillSource {
        /// Loaded from the configured skill root (the only producer today).
        Bundled,
    }

    /// A loaded skill — the immutable runtime content exposed to agents.
    ///
    /// Derives `Serialize` solely to pin the wire shape in a snapshot test; nothing
    /// reconstructs a `SkillDefinition` from JSON (the loader builds it from
    /// markdown, and `eos-tool` reads it in-memory via the registry).
    #[derive(Debug, Clone, PartialEq, Eq, Serialize)]
    #[non_exhaustive]
    pub struct SkillDefinition {
        /// The parsed skill name and registry key.
        pub name: SkillName,
        /// A one-line description (frontmatter `description`, else a fallback).
        pub description: String,
        /// The full `SKILL.md` text.
        pub content: String,
        /// Where the skill came from.
        pub source: SkillSource,
        /// The on-disk skill directory, if loaded from one.
        pub path: Option<PathBuf>,
        /// Eagerly-loaded `references/*.md`, keyed by file stem and ordered by key.
        pub references: BTreeMap<ReferenceName, String>,
    }

    #[cfg(test)]
    mod tests {
        #![allow(clippy::unwrap_used)] // unwrap is permitted in tests (err-no-unwrap-prod)
        use super::*;

        // AC-skills-06: name validation blocks traversal; dotted stems are accepted.
        #[test]
        fn rejects_path_separators_accepts_dotted_stems() {
            // Rejected: separators, parent-dir, empty.
            assert!(SkillName::parse("../x").is_err());
            assert!(SkillName::parse("a/b").is_err());
            assert!(SkillName::parse("a\\b").is_err());
            assert!(SkillName::parse("..").is_err());
            assert!(SkillName::parse("").is_err());
            assert!(ReferenceName::parse("a/b").is_err());

            // Accepted: plain names, names with spaces, dotted stems, a bare dot.
            assert!(SkillName::parse("planner").is_ok());
            assert!(SkillName::parse("Beta Heading").is_ok());
            assert!(ReferenceName::parse("api.v2").is_ok());
            assert!(ReferenceName::parse(".").is_ok());
        }

        // AC-skills-08: the serde serialize shape is pinned by a committed snapshot.
        #[test]
        fn skill_definition_serialize_snapshot() {
            let mut references = BTreeMap::new();
            // Insert out of order to prove the BTreeMap key-sorts on serialize.
            references.insert(ReferenceName::parse("rubric").unwrap(), "RUBRIC".to_owned());
            references.insert(
                ReferenceName::parse("checklist").unwrap(),
                "CHECKLIST".to_owned(),
            );
            let definition = SkillDefinition {
                name: SkillName::parse("planner").unwrap(),
                description: "Planner skill".to_owned(),
                content: "# Planner\nbody".to_owned(),
                source: SkillSource::Bundled,
                path: Some(PathBuf::from("/skills/planner")),
                references,
            };
            insta::with_settings!({ snapshot_path => "../../tests/skills/definition/snapshots" }, {
                insta::assert_json_snapshot!("skill_definition", definition);
            });
        }
    }
}
mod error {
    //! [`SkillLoadError`] — this crate's single library error enum (`err-thiserror-lib`).

    use std::path::PathBuf;

    /// Failures raised while loading skills from the configured skill root.
    ///
    /// There is deliberately **no** malformed-frontmatter variant: matching Rust's
    /// `parse_markdown_frontmatter`, broken YAML is swallowed and the loader falls
    /// back to the heading/first-paragraph metadata scan rather than failing the load.
    #[derive(Debug, thiserror::Error)]
    #[non_exhaustive]
    pub enum SkillLoadError {
        /// The skill root exists but is not a directory — a config error, so the
        /// loader fails fast rather than treating it as "no skills".
        #[error("skill root is not a directory: {0}")]
        RootNotDir(PathBuf),
        /// Listing a directory (the root or a `references/` subdirectory) failed.
        #[error("failed to read skill directory {path}")]
        ReadDir {
            /// The directory whose listing failed.
            path: PathBuf,
            /// The underlying I/O error.
            #[source]
            cause: std::io::Error,
        },
        /// Reading a `SKILL.md` or `references/*.md` file failed.
        #[error("failed to read skill file {path}")]
        ReadFile {
            /// The file whose read failed.
            path: PathBuf,
            /// The underlying I/O error.
            #[source]
            cause: std::io::Error,
        },
        /// A parsed skill or reference name was empty or carried a path component
        /// (defense-in-depth; see [`super::SkillName`]).
        #[error("invalid skill name {0:?}")]
        InvalidName(String),
    }
}
mod registry {
    //! [`SkillRegistry`] — an immutable, name-keyed skill lookup over a `BTreeMap`.
    //!
    //! The `BTreeMap<SkillName, _>` makes `list_skills` ordering an invariant of the
    //! data structure rather than a per-call sort (Rust `registry.py` sorts on
    //! every `list_skills`). The filesystem constructor lives in
    //! [`super::loader`]; this module owns the in-memory contract only.

    use std::collections::BTreeMap;

    use super::definition::{SkillDefinition, SkillName};

    /// Stores loaded skills by [`SkillName`]. Built once at the composition root and
    /// then shared immutably as `Arc<SkillRegistry>`.
    #[derive(Debug, Clone, PartialEq, Eq, Default)]
    pub struct SkillRegistry {
        pub(crate) skills: BTreeMap<SkillName, SkillDefinition>,
    }

    impl SkillRegistry {
        /// An empty registry.
        #[must_use]
        pub fn new() -> Self {
            Self::default()
        }

        /// Insert one skill, replacing any same-named entry (last-wins, matching the
        /// Rust dict assignment `self._skills[skill.name] = skill`).
        pub fn register(&mut self, skill: SkillDefinition) {
            self.skills.insert(skill.name.clone(), skill);
        }

        /// Look up a skill by name; `None` if absent.
        #[must_use]
        pub fn get(&self, name: &SkillName) -> Option<&SkillDefinition> {
            self.skills.get(name)
        }

        /// All skills in [`SkillName`] order (the `BTreeMap` guarantees the sort).
        pub fn list_skills(&self) -> impl Iterator<Item = &SkillDefinition> {
            self.skills.values()
        }
    }

    #[cfg(test)]
    mod tests {
        #![allow(clippy::unwrap_used)] // unwrap is permitted in tests (err-no-unwrap-prod)
        use super::*;
        use crate::tools::skills::definition::SkillSource;

        fn def(name: &str, description: &str) -> SkillDefinition {
            SkillDefinition {
                name: SkillName::parse(name).unwrap(),
                description: description.to_owned(),
                content: String::new(),
                source: SkillSource::Bundled,
                path: None,
                references: BTreeMap::new(),
            }
        }

        // AC-skills-07: register is last-wins by SkillName; get returns Option;
        // list_skills is SkillName-sorted.
        #[test]
        fn register_get_list_semantics() {
            let mut registry = SkillRegistry::new();
            registry.register(def("banana", "first"));
            registry.register(def("apple", "first"));

            // get: hit and miss.
            assert_eq!(
                registry.get(&SkillName::parse("apple").unwrap()),
                Some(&def("apple", "first"))
            );
            assert!(registry
                .get(&SkillName::parse("missing").unwrap())
                .is_none());

            // last-wins: re-registering "apple" replaces it.
            registry.register(def("apple", "second"));
            assert_eq!(
                registry
                    .get(&SkillName::parse("apple").unwrap())
                    .unwrap()
                    .description,
                "second"
            );

            // list_skills is key-sorted regardless of insertion order.
            let names: Vec<&str> = registry.list_skills().map(|s| s.name.as_str()).collect();
            assert_eq!(names, vec!["apple", "banana"]);
        }
    }
}
mod bundled {
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

    use super::definition::{ReferenceName, SkillDefinition, SkillName, SkillSource};
    use super::error::SkillLoadError;

    const SKILL_FILE: &str = "SKILL.md";
    const REFERENCES_DIR: &str = "references";
    const DESCRIPTION_MAX_CHARS: usize = 200;

    /// Load every directory skill under `content_dir` in sorted order.
    ///
    /// The caller ([`super::loader`]) guarantees `content_dir` is an existing
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
                if !stripped.is_empty()
                    && !stripped.starts_with("---")
                    && !stripped.starts_with('#')
                {
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
        use crate::tools::skills::support::Scratch;

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
}
mod loader {
    //! Config-rooted load orchestration (`skills/core/loader.py`).
    //!
    //! Adds the filesystem constructor [`SkillRegistry::load_from_dir`]. The Rust
    //! `cwd` parameter is **dropped** (it was always ignored, `del cwd`); the root
    //! is the explicit `skill_root` passed in by `eos-config` resolution
    //! (GC-skills-01).

    use std::path::Path;

    use super::bundled::load_bundled_skills;
    use super::error::SkillLoadError;
    use super::registry::SkillRegistry;

    impl SkillRegistry {
        /// Load a registry from an explicit skill root — the seam's only filesystem
        /// constructor.
        ///
        /// A **missing** root yields an empty registry (Rust returns `[]` when the
        /// content dir does not exist). A root that exists but is **not a directory**
        /// is a config error and yields [`SkillLoadError::RootNotDir`].
        ///
        /// # Errors
        /// Returns [`SkillLoadError`] for a non-directory root or any I/O / invalid
        /// name encountered while walking the tree.
        pub fn load_from_dir(skill_root: &Path) -> Result<Self, SkillLoadError> {
            let mut registry = Self::new();
            if !skill_root.exists() {
                return Ok(registry);
            }
            if !skill_root.is_dir() {
                return Err(SkillLoadError::RootNotDir(skill_root.to_owned()));
            }
            for skill in load_bundled_skills(skill_root)? {
                registry.register(skill);
            }
            Ok(registry)
        }
    }

    #[cfg(test)]
    mod tests {
        #![allow(clippy::unwrap_used)] // unwrap is permitted in tests (err-no-unwrap-prod)
        use std::path::PathBuf;

        use super::*;
        use crate::tools::skills::definition::{SkillName, SkillSource};
        use crate::tools::skills::support::Scratch;

        /// Restore the process working directory on drop so the cwd-mutating test
        /// cannot leak into others.
        struct CwdGuard(PathBuf);

        impl CwdGuard {
            fn capture() -> Self {
                Self(std::env::current_dir().unwrap())
            }
        }

        impl Drop for CwdGuard {
            fn drop(&mut self) {
                let _ = std::env::set_current_dir(&self.0);
            }
        }

        fn alpha_beta_fixture(name: &str) -> Scratch {
            let scratch = Scratch::new(name);
            scratch.write(
                "alpha/SKILL.md",
                "---\nname: alpha-name\ndescription: Alpha desc\n---\n# Alpha\nbody\n",
            );
            scratch.write("beta/SKILL.md", "# Beta Heading\n\nBeta paragraph text\n");
            scratch
        }

        // AC-skills-01: directory-skill parity, including the metadata fallback.
        #[test]
        fn loads_directory_skills_with_metadata_fallback() {
            let scratch = alpha_beta_fixture("dir-skills");
            let registry = SkillRegistry::load_from_dir(scratch.path()).unwrap();
            assert_eq!(registry.list_skills().count(), 2);

            // alpha: frontmatter name + description, source = Bundled.
            let alpha = registry
                .get(&SkillName::parse("alpha-name").unwrap())
                .unwrap();
            assert_eq!(alpha.description, "Alpha desc");
            assert_eq!(alpha.source, SkillSource::Bundled);
            assert_eq!(
                alpha.content,
                "---\nname: alpha-name\ndescription: Alpha desc\n---\n# Alpha\nbody\n"
            );

            // beta: heading fallback for name, first paragraph for description.
            let beta = registry
                .get(&SkillName::parse("Beta Heading").unwrap())
                .unwrap();
            assert_eq!(beta.name.as_str(), "Beta Heading");
            assert_eq!(beta.description, "Beta paragraph text");
        }

        // AC-skills-03: loading the same root twice yields equal registries.
        #[test]
        fn load_is_deterministic() {
            let scratch = alpha_beta_fixture("determinism");
            let first = SkillRegistry::load_from_dir(scratch.path()).unwrap();
            let second = SkillRegistry::load_from_dir(scratch.path()).unwrap();
            assert_eq!(first, second);
        }

        // AC-skills-04: the result is independent of the process working directory.
        #[test]
        fn ignores_process_cwd() {
            let scratch = alpha_beta_fixture("ignore-cwd");
            let root = scratch.path().to_owned(); // absolute
            let _guard = CwdGuard::capture();

            std::env::set_current_dir(std::env::temp_dir()).unwrap();
            let first = SkillRegistry::load_from_dir(&root).unwrap();

            let elsewhere = Scratch::new("ignore-cwd-elsewhere");
            std::env::set_current_dir(elsewhere.path()).unwrap();
            let second = SkillRegistry::load_from_dir(&root).unwrap();

            assert_eq!(first, second);
        }

        // AC-skills-05: a missing root is empty; a file root is RootNotDir.
        #[test]
        fn missing_root_empty_nondir_root_errors() {
            let missing = Path::new("/no/such/skills/root");
            assert_eq!(
                SkillRegistry::load_from_dir(missing)
                    .unwrap()
                    .list_skills()
                    .count(),
                0
            );

            let scratch = Scratch::new("nondir-root");
            let file = scratch.write("not_a_dir", "x");
            let err = SkillRegistry::load_from_dir(&file).unwrap_err();
            assert!(matches!(err, SkillLoadError::RootNotDir(_)), "{err:?}");
        }
    }
}
mod load_skill_reference {
    //! The `load_skill_reference` tool — serves one named `references/*.md` document
    //! from the bound agent's own skill. The skill *content* comes from the shared
    //! [`SkillRegistry`](super::SkillRegistry) captured by this executor; the
    //! per-agent **allowlist** ([`CallerScope::skill_slug`](super::CallerScope),
    //! baked in at registration) scopes which skill the caller may read: an agent
    //! reads only its own skill's references, and a not-found error lists only that
    //! skill.

    use std::sync::Arc;

    use async_trait::async_trait;
    use eos_types::JsonObject;
    use schemars::{schema_for, JsonSchema};
    use serde::{Deserialize, Serialize};
    use serde_json::json;

    use super::{ReferenceName, SkillName};
    use crate::registry::text_spec;
    use crate::registry::ToolConfigSet;
    use crate::tools::parse_input;
    use crate::tools::CallerScope;
    use crate::tools::SkillHandle;
    use crate::ExecutionMetadata;
    use crate::ToolError;
    use crate::ToolExecutor;
    use crate::ToolName;
    use crate::ToolRegistry;
    use crate::{OutputShape, ToolResult};

    #[derive(Debug, Deserialize, Serialize, JsonSchema)]
    pub(super) struct LoadSkillReferenceInput {
        /// Name of the skill that owns the reference.
        skill_name: String,
        /// Exact reference document name to load.
        reference_name: String,
    }

    /// The `load_skill_reference` executor, scoped to the caller's own skill(s). The
    /// `allowed` list is built from the bound agent's declared skill at
    /// registration; empty ⇒ a no-op tool that errors on every call.
    struct LoadSkillReference {
        allowed: Vec<SkillName>,
        service: SkillHandle,
    }

    #[async_trait]
    impl ToolExecutor for LoadSkillReference {
        async fn execute(
            &self,
            input: &JsonObject,
            _ctx: &ExecutionMetadata,
        ) -> Result<ToolResult, ToolError> {
            let parsed: LoadSkillReferenceInput =
                match parse_input(ToolName::LoadSkillReference, input) {
                    Ok(v) => v,
                    Err(err) => return Ok(err),
                };
            // Only the agent's own declared skill(s), resolved against the shared
            // registry, are readable.
            let available: Vec<String> = self
                .allowed
                .iter()
                .filter_map(|slug| self.service.skill_registry.get(slug))
                .map(|s| s.name.as_str().to_owned())
                .collect();

            if !available.iter().any(|name| name == &parsed.skill_name) {
                return Ok(ToolResult::error(
                    json!({
                        "error": format!("Skill '{}' not found.", parsed.skill_name),
                        "available": available,
                    })
                    .to_string(),
                ));
            }

            let skill = SkillName::parse(parsed.skill_name.clone())
                .ok()
                .and_then(|name| self.service.skill_registry.get(&name));
            let Some(skill) = skill else {
                return Ok(ToolResult::error(format!(
                    "Skill '{}' not found in registry.",
                    parsed.skill_name
                )));
            };

            let content = ReferenceName::parse(parsed.reference_name.clone())
                .ok()
                .and_then(|reference| skill.references.get(&reference));
            match content {
                Some(content) => Ok(ToolResult::ok(content.clone())),
                None => {
                    let available_references: Vec<String> = skill
                        .references
                        .keys()
                        .map(|r| r.as_str().to_owned())
                        .collect();
                    Ok(ToolResult::error(
                        json!({
                            "error": format!(
                                "Reference '{}' not found in skill '{}'.",
                                parsed.reference_name, parsed.skill_name
                            ),
                            "available_references": available_references,
                        })
                        .to_string(),
                    ))
                }
            }
        }
    }

    pub(super) fn register(
        registry: &mut ToolRegistry,
        config: &ToolConfigSet,
        caller: &CallerScope,
        skill_service: SkillHandle,
    ) {
        // Scope to the caller's own skill folder slug (0-or-1 entries).
        let allowed: Vec<SkillName> = caller
            .skill_slug
            .as_deref()
            .and_then(|slug| SkillName::parse(slug.to_owned()).ok())
            .into_iter()
            .collect();
        let cfg = config.get(ToolName::LoadSkillReference);
        crate::tools::register_tool(
            registry,
            ToolName::LoadSkillReference,
            cfg,
            text_spec(
                ToolName::LoadSkillReference,
                &cfg.description,
                schema_for!(LoadSkillReferenceInput),
            ),
            OutputShape::Text,
            Arc::new(LoadSkillReference {
                allowed,
                service: skill_service,
            }),
        );
    }

    #[cfg(test)]
    mod tests {
        #![allow(clippy::unwrap_used)] // unwrap permitted in tests (err-no-unwrap-prod)

        use std::fs;
        use std::path::{Path, PathBuf};

        use serde_json::Value;

        use super::*;
        use crate::support::metadata;
        use crate::tools::SkillRegistry;

        /// Throwaway skill root under the temp dir, removed on drop (the loader is
        /// filesystem-backed and `SkillDefinition` is `#[non_exhaustive]`, so a real
        /// registry must come from disk). Mirrors the tool-owned skill test scratch.
        struct Scratch(PathBuf);
        impl Scratch {
            fn new(name: &str) -> Self {
                let nonce = std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap()
                    .as_nanos();
                let dir = std::env::temp_dir()
                    .join(format!("eos-tool-{name}-{}-{nonce}", std::process::id()));
                let _ = fs::remove_dir_all(&dir);
                fs::create_dir_all(&dir).unwrap();
                Self(dir)
            }
            fn write(&self, rel: &str, body: &str) {
                let path = self.0.join(rel);
                fs::create_dir_all(path.parent().unwrap()).unwrap();
                fs::write(path, body).unwrap();
            }
            fn path(&self) -> &Path {
                &self.0
            }
        }
        impl Drop for Scratch {
            fn drop(&mut self) {
                let _ = fs::remove_dir_all(&self.0);
            }
        }

        /// A two-skill registry: `a` (reference `ref_a`) and `b` (reference `secret`).
        fn two_skill_registry(scratch: &Scratch) -> SkillRegistry {
            scratch.write(
                "a/SKILL.md",
                "---\nname: a\ndescription: skill a\n---\nbody a\n",
            );
            scratch.write("a/references/ref_a.md", "REF A CONTENT");
            scratch.write(
                "b/SKILL.md",
                "---\nname: b\ndescription: skill b\n---\nbody b\n",
            );
            scratch.write("b/references/secret.md", "SECRET B CONTENT");
            SkillRegistry::load_from_dir(scratch.path()).unwrap()
        }

        fn service_with(registry: SkillRegistry) -> SkillHandle {
            SkillHandle::new(Arc::new(registry))
        }

        fn input(skill: &str, reference: &str) -> JsonObject {
            let mut m = JsonObject::new();
            m.insert("skill_name".to_owned(), Value::String(skill.to_owned()));
            m.insert(
                "reference_name".to_owned(),
                Value::String(reference.to_owned()),
            );
            m
        }

        fn scoped_to(slug: &str, service: SkillHandle) -> LoadSkillReference {
            LoadSkillReference {
                allowed: vec![SkillName::parse(slug.to_owned()).unwrap()],
                service,
            }
        }

        // D7: an agent scoped to skill `a` cannot read skill `b`'s references; the
        // content never leaks and the not-found error lists only `a` (never `b`,
        // never the whole registry).
        #[tokio::test]
        async fn scoped_agent_cannot_read_other_skill() {
            let scratch = Scratch::new("d7-isolation");
            let ctx = metadata();
            let res = scoped_to("a", service_with(two_skill_registry(&scratch)))
                .execute(&input("b", "secret"), &ctx)
                .await
                .unwrap();

            assert!(res.is_error, "reading another skill is denied");
            assert!(
                !res.output.contains("SECRET B CONTENT"),
                "content never leaks: {}",
                res.output
            );
            let body: Value = serde_json::from_str(&res.output).unwrap();
            assert_eq!(
                body["available"],
                json!(["a"]),
                "error lists only the agent's own skill, not all bundled skills"
            );
        }

        // D7: the agent CAN read its own skill's reference.
        #[tokio::test]
        async fn scoped_agent_reads_own_reference() {
            let scratch = Scratch::new("d7-own");
            let ctx = metadata();
            let res = scoped_to("a", service_with(two_skill_registry(&scratch)))
                .execute(&input("a", "ref_a"), &ctx)
                .await
                .unwrap();

            assert!(!res.is_error, "own reference is served: {}", res.output);
            assert_eq!(res.output, "REF A CONTENT");
        }

        // D7: a skill-less agent (empty allowlist) is a no-op tool — every call errors
        // with an empty `available` (Rust `allowed_slugs=[]`).
        #[tokio::test]
        async fn skill_less_agent_has_empty_allowlist() {
            let scratch = Scratch::new("d7-noskill");
            let ctx = metadata();
            let res = LoadSkillReference {
                allowed: vec![],
                service: service_with(two_skill_registry(&scratch)),
            }
            .execute(&input("a", "ref_a"), &ctx)
            .await
            .unwrap();

            assert!(res.is_error);
            let body: Value = serde_json::from_str(&res.output).unwrap();
            assert_eq!(body["available"], json!([]), "no skill in scope");
        }

        // D7 wiring: `register` builds the allowlist from `CallerScope::skill_slug`
        // and sources its config (intent/terminal/hooks/description) from the set.
        #[test]
        fn register_scopes_to_caller_skill_slug() {
            let mut registry = ToolRegistry::new();
            register(
                &mut registry,
                &crate::tools::repo_tools_config(),
                &CallerScope {
                    dispatchable_subagents: vec![],
                    skill_slug: Some("a".to_owned()),
                },
                SkillHandle::new(Arc::new(SkillRegistry::new())),
            );
            assert!(
                registry.get(ToolName::LoadSkillReference).is_some(),
                "load_skill_reference is registered for a skill-bound caller"
            );
        }
    }
}

pub use definition::{ReferenceName, SkillDefinition, SkillName, SkillSource};
pub use error::SkillLoadError;
pub use registry::SkillRegistry;

pub(crate) fn register(
    registry: &mut crate::ToolRegistry,
    config: &crate::registry::ToolConfigSet,
    caller: &crate::tools::CallerScope,
    skills: crate::tools::SkillHandle,
) {
    load_skill_reference::register(registry, config, caller, skills);
}

pub(crate) fn register_schema(
    registry: &mut crate::ToolRegistry,
    config: &crate::registry::ToolConfigSet,
    _caller: &crate::tools::CallerScope,
) {
    use crate::registry::text_spec;
    use crate::{OutputShape, ToolName};
    use schemars::schema_for;

    let cfg = config.get(ToolName::LoadSkillReference);
    crate::tools::register_schema_tool(
        registry,
        ToolName::LoadSkillReference,
        cfg,
        text_spec(
            ToolName::LoadSkillReference,
            &cfg.description,
            schema_for!(load_skill_reference::LoadSkillReferenceInput),
        ),
        OutputShape::Text,
    );
}
