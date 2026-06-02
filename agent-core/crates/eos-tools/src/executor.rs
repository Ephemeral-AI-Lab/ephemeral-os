//! [`ToolExecutor`] — the object-safe execute seam — and [`RegisteredTool`],
//! the bundle of an executor with its static registry metadata.

use std::sync::Arc;

use async_trait::async_trait;
use eos_llm_client::ToolSpec;
use eos_types::JsonObject;

use crate::error::ToolError;
use crate::hooks::Hook;
use crate::intent::ToolIntent;
use crate::metadata::ExecutionMetadata;
use crate::name::ToolName;
use crate::result::{OutputShape, ToolResult};

/// Execute against already-parsed, hook-validated input.
///
/// Used behind `dyn` in the registry (heterogeneous tool storage), so it carries
/// `#[async_trait]` (native async-fn-in-trait is not yet `dyn`-safe, anchor §6).
/// The executor self-parses its typed input from `input` (the framework applies
/// only the generic `background`-key rejection); a tool-domain failure (bad args,
/// "tool said no") is an in-band [`ToolResult`]`{is_error:true}` returned as `Ok`,
/// while a framework fault is [`ToolError`] (`error.rs`).
#[async_trait]
pub trait ToolExecutor: Send + Sync {
    /// Run the tool body.
    async fn execute(
        &self,
        input: &JsonObject,
        ctx: &ExecutionMetadata,
    ) -> Result<ToolResult, ToolError>;
}

/// An executor bundled with its static metadata. Built once at composition;
/// stored in the immutable [`ToolRegistry`](crate::ToolRegistry).
#[derive(Clone)]
pub struct RegisteredTool {
    /// The typed tool name (the registry key).
    pub name: ToolName,
    /// Batch-dispatch / sandbox-routing classification.
    pub intent: ToolIntent,
    /// Whether a successful call ends the agent run (stamped by the pipeline).
    pub is_terminal: bool,
    /// The neutral model-facing declaration (owned by `eos-llm-client`, §5a).
    pub spec: ToolSpec,
    /// The pre-hooks run before the body, in order.
    pub hooks: Vec<Hook>,
    /// The declared output shape the pipeline validates against.
    pub(crate) output: OutputShape,
    /// The executor implementation.
    pub(crate) executor: Arc<dyn ToolExecutor>,
}

impl std::fmt::Debug for RegisteredTool {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("RegisteredTool")
            .field("name", &self.name)
            .field("intent", &self.intent)
            .field("is_terminal", &self.is_terminal)
            .field("hooks", &self.hooks)
            .field("output", &self.output)
            .finish_non_exhaustive()
    }
}

impl RegisteredTool {
    /// Build a registered tool with no hooks.
    #[must_use]
    pub fn new(
        name: ToolName,
        intent: ToolIntent,
        is_terminal: bool,
        spec: ToolSpec,
        output: OutputShape,
        executor: Arc<dyn ToolExecutor>,
    ) -> Self {
        Self {
            name,
            intent,
            is_terminal,
            spec,
            hooks: Vec::new(),
            output,
            executor,
        }
    }

    /// Attach the pre-hooks (builder-style).
    #[must_use]
    pub fn with_hooks(mut self, hooks: Vec<Hook>) -> Self {
        self.hooks = hooks;
        self
    }

    /// The declared output shape.
    pub(crate) fn output(&self) -> &OutputShape {
        &self.output
    }

    /// The executor implementation.
    pub(crate) fn executor(&self) -> &dyn ToolExecutor {
        &*self.executor
    }
}
