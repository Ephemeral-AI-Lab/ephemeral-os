//! [`SkillRegistry`] — an immutable, name-keyed skill lookup over a `BTreeMap`.
//!
//! The `BTreeMap<SkillName, _>` makes `list_skills` ordering an invariant of the
//! data structure rather than a per-call sort (Rust `registry.py` sorts on
//! every `list_skills`). The filesystem constructor lives in
//! [`crate::loader`]; this module owns the in-memory contract only.

use std::collections::BTreeMap;

use crate::definition::{SkillDefinition, SkillName};

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
    use crate::definition::SkillSource;

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
