//! [`ToolRegistry`] — the insertion-ordered, [`ToolName`]-keyed tool store.
//!
//! Ports `_framework/core/registry.py`. Keyed by [`ToolName`] (not `String`) for
//! OCP via registration (anchor §6, no `match` in dispatch). Insertion order is
//! preserved (`Vec` + index map) so [`ToolRegistry::specs`] is deterministic for
//! the Phase-4 schema-parity snapshot. Built once at composition and shared
//! immutably as `Arc<ToolRegistry>`; `restrict`/`remove` run during per-agent
//! construction before sharing.

use std::collections::HashMap;

use eos_llm_client::ToolSpec;

use crate::executor::RegisteredTool;
use crate::name::ToolName;

/// An insertion-ordered registry of [`RegisteredTool`]s.
#[derive(Debug, Default)]
pub struct ToolRegistry {
    tools: Vec<RegisteredTool>,
    index: HashMap<ToolName, usize>,
}

impl ToolRegistry {
    /// An empty registry.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Register a tool. Re-registering a name replaces it **in place** (keeping
    /// its position), mirroring the Python dict assignment.
    pub fn register(&mut self, tool: RegisteredTool) {
        if let Some(&idx) = self.index.get(&tool.name) {
            self.tools[idx] = tool;
        } else {
            let idx = self.tools.len();
            self.index.insert(tool.name, idx);
            self.tools.push(tool);
        }
    }

    /// Look up a tool by name.
    #[must_use]
    pub fn get(&self, name: ToolName) -> Option<&RegisteredTool> {
        self.index.get(&name).map(|&idx| &self.tools[idx])
    }

    /// Iterate tools in insertion order.
    pub fn list(&self) -> impl Iterator<Item = &RegisteredTool> {
        self.tools.iter()
    }

    /// Remove the named tools (no-op for absent names).
    pub fn remove(&mut self, names: &[ToolName]) {
        let drop: std::collections::HashSet<ToolName> = names.iter().copied().collect();
        self.tools.retain(|tool| !drop.contains(&tool.name));
        self.reindex();
    }

    /// Keep only the named tools, preserving their current order.
    pub fn restrict(&mut self, names: &[ToolName]) {
        let keep: std::collections::HashSet<ToolName> = names.iter().copied().collect();
        self.tools.retain(|tool| keep.contains(&tool.name));
        self.reindex();
    }

    /// The model-facing specs in insertion order (replaces `to_api_schema`).
    #[must_use]
    pub fn specs(&self) -> Vec<ToolSpec> {
        self.tools.iter().map(|tool| tool.spec.clone()).collect()
    }

    /// The number of registered tools.
    #[must_use]
    pub fn len(&self) -> usize {
        self.tools.len()
    }

    /// Whether the registry is empty.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.tools.is_empty()
    }

    fn reindex(&mut self) {
        self.index.clear();
        for (idx, tool) in self.tools.iter().enumerate() {
            self.index.insert(tool.name, idx);
        }
    }
}
