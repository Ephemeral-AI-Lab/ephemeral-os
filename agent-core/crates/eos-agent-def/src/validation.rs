//! The *pure* fragments of profile validation that need no other crate: the
//! `context_recipe` role-gating precheck (`resolved_validation.py:42`) and the
//! skill-file terminal-silence scanner (`skills/loader.py`).
//!
//! The cyclic edges in the Python source are broken by relocation and injection
//! (GC-eos-agent-def-05): the recipe *catalog* check (`validate_context_recipe`)
//! lives in `eos-workflow`; the terminal keys are passed into the scanner as data
//! rather than imported from `eos-tools`.

use crate::error::AgentDefError;
use crate::model::{AgentDefinition, AgentRole};

/// Roles that own a context builder; a `context_recipe` is only valid on these.
fn role_has_context_builder(role: AgentRole) -> bool {
    matches!(
        role,
        AgentRole::Planner | AgentRole::Generator | AgentRole::Reducer
    )
}

/// Reject a `context_recipe` declared by a role outside
/// `{planner, generator, reducer}` (the pure precheck only).
///
/// The catalog-validity check (`validate_context_recipe`) is owned by
/// `eos-workflow` and runs at the composition root after the registry is built.
///
/// # Errors
/// Returns [`AgentDefError::RecipeRoleMismatch`] when a recipe is declared by a
/// role that has no context builder.
pub fn check_context_recipe_role(definition: &AgentDefinition) -> Result<(), AgentDefError> {
    let Some(recipe) = &definition.context_recipe else {
        return Ok(());
    };
    if role_has_context_builder(definition.role) {
        return Ok(());
    }
    Err(AgentDefError::RecipeRoleMismatch {
        agent: definition.name.as_str().to_owned(),
        recipe: recipe.clone(),
        role: definition.role,
    })
}

/// Terminal-silence lint over a skill body (`agents/skills/loader.py`).
pub mod skill_lint {
    /// Scan a skill body for terminal-tool mentions, returning one
    /// human-readable violation per hit (empty means clean).
    ///
    /// `terminal_keys` are injected as data (the `TERMINAL_DESCRIPTORS` keys are
    /// owned by `eos-tools`, GC-eos-agent-def-05). The caller passes an already
    /// frontmatter-stripped `body` so author metadata cannot false-positive.
    #[must_use]
    pub fn scan_skill_file(body: &str, terminal_keys: &[&str]) -> Vec<String> {
        let submit_hits = find_submit_tokens(body);
        let mut violations: Vec<String> = submit_hits
            .iter()
            .map(|hit| {
                format!(
                    "skill body mentions terminal-tool name {hit:?}; row 4 must be \
                     terminal-silent (row 3 owns the catalog)"
                )
            })
            .collect();

        // Catch terminal keys that escape the `submit_*` pattern.
        for key in terminal_keys {
            if submit_hits.iter().any(|hit| hit == key) {
                continue;
            }
            if body.contains(*key) {
                violations.push(format!(
                    "skill body mentions terminal descriptor key {key:?}; row 4 must be \
                     terminal-silent (row 3 owns the catalog)"
                ));
            }
        }
        violations
    }

    /// Sorted, de-duplicated `submit_<identifier>` tokens (port of the
    /// `submit_[A-Za-z0-9_]+` regex without a regex dependency).
    fn find_submit_tokens(body: &str) -> Vec<String> {
        const PREFIX: &str = "submit_";
        let bytes = body.as_bytes();
        let mut tokens = Vec::new();
        let mut cursor = 0;
        while let Some(rel) = body[cursor..].find(PREFIX) {
            let start = cursor + rel;
            let extend_from = start + PREFIX.len();
            let mut end = extend_from;
            while end < bytes.len() && (bytes[end].is_ascii_alphanumeric() || bytes[end] == b'_') {
                end += 1;
            }
            // The regex `+` requires at least one identifier char after the
            // underscore; a bare `submit_` followed by a non-word char is no match.
            if end > extend_from {
                tokens.push(body[start..end].to_owned());
            }
            cursor = end.max(extend_from);
        }
        tokens.sort();
        tokens.dedup();
        tokens
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // unwrap is permitted in tests (err-no-unwrap-prod)
    use std::num::NonZeroU32;

    use super::skill_lint::scan_skill_file;
    use super::*;
    use crate::model::{AgentName, AgentType};

    fn def_with(role: AgentRole, recipe: Option<&str>) -> AgentDefinition {
        AgentDefinition {
            name: AgentName::new("a").unwrap(),
            description: "d".to_owned(),
            system_prompt: None,
            model: None,
            tool_call_limit: NonZeroU32::new(10).unwrap(),
            role,
            agent_type: AgentType::Agent,
            allowed_tools: vec![],
            terminals: vec!["submit_x".to_owned()],
            notification_triggers: vec![],
            skill: None,
            context_recipe: recipe.map(str::to_owned),
        }
    }

    // AC-eos-agent-def-07: recipe on an out-of-scope role fails; in-scope passes.
    #[test]
    fn recipe_role_precheck() {
        // No recipe -> ok regardless of role.
        assert!(check_context_recipe_role(&def_with(AgentRole::Helper, None)).is_ok());
        // In-scope roles pass the precheck.
        for role in [AgentRole::Planner, AgentRole::Generator, AgentRole::Reducer] {
            assert!(check_context_recipe_role(&def_with(role, Some("generator"))).is_ok());
        }
        // Out-of-scope role with a recipe -> RecipeRoleMismatch.
        for role in [AgentRole::Root, AgentRole::Helper, AgentRole::Subagent] {
            let err = check_context_recipe_role(&def_with(role, Some("generator"))).unwrap_err();
            assert!(
                matches!(err, AgentDefError::RecipeRoleMismatch { .. }),
                "{err:?}"
            );
        }
    }

    // AC-eos-agent-def-08: submit_* tokens and injected keys are flagged; a
    // terminal-silent body returns empty.
    #[test]
    fn skill_lint_detects_terminals() {
        let keys = ["submit_planner_outcome", "finish_run"];

        let submit = scan_skill_file("call submit_planner_outcome when done", &keys);
        assert_eq!(submit.len(), 1, "{submit:?}");
        assert!(submit[0].contains("submit_planner_outcome"));

        let key_only = scan_skill_file("then perform the finish_run step", &keys);
        assert_eq!(key_only.len(), 1, "{key_only:?}");
        assert!(key_only[0].contains("finish_run"));

        let clean = scan_skill_file("reach the decision point and submit once", &keys);
        assert!(clean.is_empty(), "{clean:?}");
    }

    #[test]
    fn skill_lint_dedupes_and_ignores_bare_prefix() {
        // Repeated token reported once; bare `submit_` (no trailing word char) ignored.
        let hits = scan_skill_file("submit_x then submit_x and a bare submit_ here", &[]);
        assert_eq!(hits.len(), 1, "{hits:?}");
        assert!(hits[0].contains("submit_x"));
    }
}
