//! Config-rooted load orchestration (`skills/core/loader.py`).
//!
//! Adds the filesystem constructor [`SkillRegistry::load_from_dir`] and the
//! free-function alias [`load_skill_registry`] that `eos-runtime` calls. The
//! Python `cwd` parameter is **dropped** (it was always ignored, `del cwd`); the
//! root is the explicit `skill_root` passed in by `eos-config` resolution
//! (GC-skills-01).

use std::path::Path;

use crate::bundled::load_bundled_skills;
use crate::error::SkillLoadError;
use crate::registry::SkillRegistry;

impl SkillRegistry {
    /// Load a registry from an explicit skill root — the seam's only filesystem
    /// constructor.
    ///
    /// A **missing** root yields an empty registry (Python returns `[]` when the
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

/// Composition-root entry point: load the skill registry from `skill_root`.
///
/// A thin alias for [`SkillRegistry::load_from_dir`] giving `eos-runtime` a name
/// parallel to the Python `load_skill_registry`; the filesystem logic lives in
/// exactly one place.
///
/// # Errors
/// Propagates [`SkillLoadError`] from [`SkillRegistry::load_from_dir`].
pub fn load_skill_registry(skill_root: &Path) -> Result<SkillRegistry, SkillLoadError> {
    SkillRegistry::load_from_dir(skill_root)
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // unwrap is permitted in tests (err-no-unwrap-prod)
    use std::path::PathBuf;

    use super::*;
    use crate::definition::{SkillName, SkillSource};
    use crate::test_support::Scratch;

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
        let registry = load_skill_registry(scratch.path()).unwrap();
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
        let first = load_skill_registry(scratch.path()).unwrap();
        let second = load_skill_registry(scratch.path()).unwrap();
        assert_eq!(first, second);
    }

    // AC-skills-04: the result is independent of the process working directory.
    #[test]
    fn ignores_process_cwd() {
        let scratch = alpha_beta_fixture("ignore-cwd");
        let root = scratch.path().to_owned(); // absolute
        let _guard = CwdGuard::capture();

        std::env::set_current_dir(std::env::temp_dir()).unwrap();
        let first = load_skill_registry(&root).unwrap();

        let elsewhere = Scratch::new("ignore-cwd-elsewhere");
        std::env::set_current_dir(elsewhere.path()).unwrap();
        let second = load_skill_registry(&root).unwrap();

        assert_eq!(first, second);
    }

    // AC-skills-05: a missing root is empty; a file root is RootNotDir.
    #[test]
    fn missing_root_empty_nondir_root_errors() {
        let missing = Path::new("/no/such/skills/root");
        assert_eq!(
            load_skill_registry(missing).unwrap().list_skills().count(),
            0
        );

        let scratch = Scratch::new("nondir-root");
        let file = scratch.write("not_a_dir", "x");
        let err = load_skill_registry(&file).unwrap_err();
        assert!(matches!(err, SkillLoadError::RootNotDir(_)), "{err:?}");
    }
}
