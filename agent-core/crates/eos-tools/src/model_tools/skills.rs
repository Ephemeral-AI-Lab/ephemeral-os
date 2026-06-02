//! The `load_skill_reference` tool — serves one named `references/*.md` document
//! from the per-agent [`SkillRegistry`](eos_skills::SkillRegistry) in
//! [`ExecutionMetadata`].

use std::sync::Arc;

use async_trait::async_trait;
use eos_skills::{ReferenceName, SkillName};
use eos_types::JsonObject;
use schemars::{schema_for, JsonSchema};
use serde::{Deserialize, Serialize};
use serde_json::json;

use crate::error::ToolError;
use crate::execution::parse_input;
use crate::executor::ToolExecutor;
use crate::metadata::ExecutionMetadata;
use crate::name::ToolName;
use crate::registry::ToolRegistry;
use crate::result::{OutputShape, ToolResult};
use crate::spec::text_spec;

const LOAD_SKILL_REFERENCE_DESCRIPTION: &str = "Load one named reference document attached to a skill (e.g. a checklist, template, or rubric). Cheaper than loading the full skill. Use after you've read the skill's main instructions and need a specific referenced document. The reference name comes from the skill's index.";

#[derive(Debug, Deserialize, Serialize, JsonSchema)]
struct LoadSkillReferenceInput {
    /// Name of the skill that owns the reference.
    skill_name: String,
    /// Exact reference document name to load.
    reference_name: String,
}

struct LoadSkillReference;

#[async_trait]
impl ToolExecutor for LoadSkillReference {
    async fn execute(
        &self,
        input: &JsonObject,
        ctx: &ExecutionMetadata,
    ) -> Result<ToolResult, ToolError> {
        let parsed: LoadSkillReferenceInput = match parse_input(ToolName::LoadSkillReference, input)
        {
            Ok(v) => v,
            Err(err) => return Ok(err),
        };
        let available: Vec<String> = ctx
            .skill_registry
            .list_skills()
            .map(|s| s.name.as_str().to_owned())
            .collect();

        let skill = SkillName::parse(parsed.skill_name.clone())
            .ok()
            .and_then(|name| ctx.skill_registry.get(&name));
        let Some(skill) = skill else {
            return Ok(ToolResult::error(
                json!({
                    "error": format!("Skill '{}' not found.", parsed.skill_name),
                    "available": available,
                })
                .to_string(),
            ));
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

pub(crate) fn register(registry: &mut ToolRegistry) {
    super::register_tool(
        registry,
        ToolName::LoadSkillReference,
        text_spec(
            ToolName::LoadSkillReference,
            LOAD_SKILL_REFERENCE_DESCRIPTION,
            schema_for!(LoadSkillReferenceInput),
        ),
        OutputShape::Text,
        Arc::new(LoadSkillReference),
    );
}

// NOTE: a behavioral unit test for `load_skill_reference` would need to
// construct a `SkillDefinition`, but that type is `#[non_exhaustive]` in
// `eos-skills` and cannot be built from a downstream crate via a struct literal,
// and the loader is filesystem-backed. The reference-serving behavior is covered
// end-to-end at the Phase-6/7 integration layer (no AC requires a unit test).
