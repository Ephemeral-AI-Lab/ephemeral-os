//! Read-only agent lookup table (`agents/definition/registry.py`).
//!
//! The Python global mutable `_DEFINITIONS` dict (mutated only by `test_runner`)
//! is dropped (§7, YAGNI). `eos-runtime` builds the registry once at startup via
//! [`AgentRegistryBuilder`] and stores `Arc<AgentRegistry>` in `AppState`; reads
//! are lock-free `&self` lookups. No runtime mutation seam (anchor §2).

use std::collections::HashMap;
use std::sync::Arc;

use crate::model::{AgentDefinition, AgentName, AgentType};

/// Accumulates definitions, then finalizes an immutable [`AgentRegistry`].
///
/// `add` overwrites a same-named entry, preserving Python's "register or
/// replace" semantics (`registry.py:register_definition`).
#[derive(Debug, Default)]
#[must_use]
pub struct AgentRegistryBuilder {
    definitions: HashMap<AgentName, Arc<AgentDefinition>>,
}

impl AgentRegistryBuilder {
    /// Start an empty builder.
    pub fn new() -> Self {
        Self::default()
    }

    /// Register (or replace) a definition by its name.
    pub fn add(&mut self, definition: AgentDefinition) -> &mut Self {
        self.definitions
            .insert(definition.name.clone(), Arc::new(definition));
        self
    }

    /// Finalize the immutable registry.
    pub fn build(self) -> AgentRegistry {
        AgentRegistry {
            definitions: self.definitions,
        }
    }
}

impl FromIterator<AgentDefinition> for AgentRegistry {
    fn from_iter<I: IntoIterator<Item = AgentDefinition>>(iter: I) -> Self {
        let mut builder = AgentRegistryBuilder::new();
        for definition in iter {
            builder.add(definition);
        }
        builder.build()
    }
}

/// Immutable name → definition lookup, shared as `Arc<AgentRegistry>`.
#[derive(Debug)]
pub struct AgentRegistry {
    definitions: HashMap<AgentName, Arc<AgentDefinition>>,
}

impl AgentRegistry {
    /// Look up a definition by name.
    #[must_use]
    pub fn get(&self, name: &AgentName) -> Option<&Arc<AgentDefinition>> {
        self.definitions.get(name)
    }

    /// Iterate every registered definition (unordered).
    pub fn list(&self) -> impl Iterator<Item = &Arc<AgentDefinition>> {
        self.definitions.values()
    }

    /// Subagent names targetable by `run_subagent`, sorted
    /// (`registry.py:list_dispatchable_subagent_names`).
    #[must_use]
    pub fn dispatchable_subagent_names(&self) -> Vec<AgentName> {
        let mut names: Vec<AgentName> = self
            .definitions
            .values()
            .filter(|d| d.agent_type == AgentType::Subagent)
            .map(|d| d.name.clone())
            .collect();
        names.sort();
        names
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // unwrap is permitted in tests (err-no-unwrap-prod)
    use std::num::NonZeroU32;

    use super::*;
    use crate::model::AgentRole;

    fn def(name: &str, agent_type: AgentType) -> AgentDefinition {
        AgentDefinition {
            name: AgentName::new(name).unwrap(),
            description: "d".to_owned(),
            system_prompt: None,
            model: None,
            tool_call_limit: NonZeroU32::new(10).unwrap(),
            role: if agent_type == AgentType::Subagent {
                AgentRole::Subagent
            } else {
                AgentRole::Generator
            },
            agent_type,
            allowed_tools: vec![],
            terminals: vec!["submit_x".to_owned()],
            notification_triggers: vec![],
            skill: None,
            context_recipe: None,
        }
    }

    // AC-eos-agent-def-06: dispatchable names are subagents only, sorted.
    #[test]
    fn registry_lists_dispatchable_subagents() {
        let registry: AgentRegistry = [
            def("zeta", AgentType::Subagent),
            def("root", AgentType::Agent),
            def("alpha", AgentType::Subagent),
        ]
        .into_iter()
        .collect();

        let names: Vec<String> = registry
            .dispatchable_subagent_names()
            .iter()
            .map(|n| n.as_str().to_owned())
            .collect();
        assert_eq!(names, vec!["alpha".to_owned(), "zeta".to_owned()]);
    }

    #[test]
    fn registry_get_and_replace() {
        let mut builder = AgentRegistryBuilder::new();
        builder.add(def("root", AgentType::Agent));
        // Replacing by the same name keeps a single entry.
        builder.add(def("root", AgentType::Agent));
        let registry = builder.build();
        assert!(registry.get(&AgentName::new("root").unwrap()).is_some());
        assert_eq!(registry.list().count(), 1);
        assert!(registry.get(&AgentName::new("absent").unwrap()).is_none());
    }
}
