//! Plugin op registry: the `register_plugin_op`-equivalent.
//!
//! Records `(plugin_name, op_name, handler-id, intent, auto_workspace_overlay)`
//! and flushes them to the daemon dispatcher under the public op name
//! `plugin.<plugin>.<op>`. The daemon derives the live dispatch path from the
//! operation intent plus `auto_workspace_overlay`.
//!
//! `Intent::Lifecycle` is rejected at registration — LIFECYCLE is reserved for
//! sandbox lifecycle ops, not plugin tool dispatch.
//! `// PORT backend/src/sandbox/ephemeral_workspace/plugin/op_registry.py:67-142`

use eos_protocol::Intent;

use crate::error::{PluginError, Result};

/// Default for [`PluginOpRegistration::auto_workspace_overlay`].
///
/// `true` means a `WRITE_ALLOWED` handler is wrapped by the canonical
/// overlay+OCC publish path.
/// `false` opts the plugin into self-managed publish (e.g. the LSP `apply.py`
/// runtime), keeping the existing OCC publish path UNCHANGED.
/// `// PORT backend/src/sandbox/ephemeral_workspace/plugin/op_registry.py:73 — auto_workspace_overlay default True`
pub const DEFAULT_AUTO_WORKSPACE_OVERLAY: bool = true;

/// Build the public op name the daemon dispatcher registers: `plugin.<plugin>.<op>`.
/// `// PORT backend/src/sandbox/ephemeral_workspace/plugin/op_registry.py:206 — f"plugin.{plugin}.{op}"`
#[must_use]
pub fn public_op_name(plugin_name: &str, op_name: &str) -> String {
    format!("plugin.{plugin_name}.{op_name}")
}

/// One pending plugin-op registration.
///
/// The Rust daemon never holds a Python callable; the importlib path is replaced
/// by a PPC service process.
/// `// PORT backend/src/sandbox/ephemeral_workspace/plugin/op_registry.py:67-74 — _PendingRegistration`
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PluginOpRegistration {
    /// Owning plugin (must match `^[A-Za-z_][A-Za-z0-9_]*$`).
    pub plugin_name: String,
    /// Op name (non-empty).
    pub op_name: String,
    /// The intent that selects the dispatch runner. Never `Lifecycle`.
    pub intent: Intent,
    /// `false` = self-managed publish (skip the standard overlay wrapper).
    pub auto_workspace_overlay: bool,
}

impl PluginOpRegistration {
    /// Construct + validate a registration. Rejects bad names and
    /// `Intent::Lifecycle` (the registration-time gate).
    ///
    /// # Errors
    ///
    /// Returns [`PluginError::Registration`] if the plugin/op identity is invalid
    /// or if the registration tries to use [`Intent::Lifecycle`].
    /// `// PORT backend/src/sandbox/ephemeral_workspace/plugin/op_registry.py:107-121 — register_plugin_op validation`
    pub fn new(
        plugin_name: &str,
        op_name: &str,
        intent: Intent,
        auto_workspace_overlay: bool,
    ) -> Result<Self> {
        let plugin_name = plugin_name.trim();
        let op_name = op_name.trim();
        if op_name.is_empty() || !is_valid_plugin_name(plugin_name) {
            return Err(PluginError::Registration(
                "register_plugin_op requires a valid plugin_name and non-empty op_name".to_owned(),
            ));
        }
        if intent == Intent::Lifecycle {
            return Err(PluginError::Registration(
                "Intent::Lifecycle is reserved for sandbox lifecycle ops, not plugin tools"
                    .to_owned(),
            ));
        }
        Ok(Self {
            plugin_name: plugin_name.to_owned(),
            op_name: op_name.to_owned(),
            intent,
            auto_workspace_overlay,
        })
    }

    /// The public op name this registration flushes under.
    #[must_use]
    pub fn public_op_name(&self) -> String {
        public_op_name(&self.plugin_name, &self.op_name)
    }
}

/// Whether `name` matches the Python `_PLUGIN_NAME_RE` (`^[A-Za-z_][A-Za-z0-9_]*$`).
/// `// PORT backend/src/sandbox/ephemeral_workspace/plugin/op_registry.py:78 — _PLUGIN_NAME_RE`
fn is_valid_plugin_name(name: &str) -> bool {
    let mut chars = name.chars();
    match chars.next() {
        Some(c) if c == '_' || c.is_ascii_alphabetic() => {}
        _ => return false,
    }
    chars.all(|c| c == '_' || c.is_ascii_alphanumeric())
}

/// The pending-registration table the decorator appends to and `flush` drains.
///
/// Keyed on `(plugin_name, op_name)`; identical re-registration is a no-op,
/// conflicting registration with a different handler errors.
/// `// PORT backend/src/sandbox/ephemeral_workspace/plugin/op_registry.py:76 — _PENDING`
#[derive(Debug, Default)]
pub struct OpRegistry {
    pending: Vec<PluginOpRegistration>,
}

impl OpRegistry {
    /// A fresh, empty registry.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Record one validated registration. Identical re-registration (same
    /// plugin/op/intent/flag) is a no-op; a conflicting `(plugin, op)` errors.
    ///
    /// # Errors
    ///
    /// Returns [`PluginError::Conflict`] when the same public op is registered
    /// with different metadata.
    /// `// PORT backend/src/sandbox/ephemeral_workspace/plugin/op_registry.py:123-142 — decorator`
    pub fn register(&mut self, registration: PluginOpRegistration) -> Result<()> {
        if let Some(existing) = self.pending.iter().find(|r| {
            r.plugin_name == registration.plugin_name && r.op_name == registration.op_name
        }) {
            if *existing == registration {
                return Ok(());
            }
            return Err(PluginError::Conflict(registration.public_op_name()));
        }
        self.pending.push(registration);
        Ok(())
    }

    /// Pending registrations, optionally filtered by plugin.
    /// `// PORT backend/src/sandbox/ephemeral_workspace/plugin/op_registry.py:145-155 — pending_plugin_registrations`
    #[must_use]
    pub fn pending(&self, plugin_name: Option<&str>) -> Vec<&PluginOpRegistration> {
        self.pending
            .iter()
            .filter(|r| plugin_name.is_none_or(|p| r.plugin_name == p))
            .collect()
    }

    /// Drop pending registrations for one plugin before a runtime reload.
    /// `// PORT backend/src/sandbox/ephemeral_workspace/plugin/op_registry.py:158-165 — clear_plugin_registrations`
    pub fn clear(&mut self, plugin_name: &str) {
        self.pending.retain(|r| r.plugin_name != plugin_name);
    }

    /// Drain pending registrations for `plugin_name`, returning the public op
    /// names the daemon dispatcher should register. The caller owns live route
    /// selection from each entry's intent plus `auto_workspace_overlay`.
    /// `// PORT backend/src/sandbox/ephemeral_workspace/plugin/op_registry.py:168-221 — flush_plugin_registrations`
    pub fn flush(&mut self, plugin_name: &str) -> Vec<String> {
        let mut registered = Vec::new();
        self.pending.retain(|r| {
            if r.plugin_name == plugin_name {
                registered.push(r.public_op_name());
                false
            } else {
                true
            }
        });
        registered
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    type TestResult = std::result::Result<(), PluginError>;

    #[test]
    fn public_op_name_format() {
        assert_eq!(public_op_name("lsp", "hover"), "plugin.lsp.hover");
    }

    #[test]
    fn plugin_name_validation() {
        assert!(is_valid_plugin_name("lsp"));
        assert!(is_valid_plugin_name("_x9"));
        assert!(!is_valid_plugin_name("9lsp"));
        assert!(!is_valid_plugin_name(""));
        assert!(!is_valid_plugin_name("ls-p"));
    }

    #[test]
    fn lifecycle_intent_rejected_at_registration() {
        assert!(matches!(
            PluginOpRegistration::new("lsp", "hover", Intent::Lifecycle, true),
            Err(PluginError::Registration(_))
        ));
        assert!(PluginOpRegistration::new("lsp", "hover", Intent::ReadOnly, true).is_ok());
    }

    #[test]
    fn conflicting_handler_errors_idempotent_is_noop() -> TestResult {
        let mut reg = OpRegistry::new();
        let a = PluginOpRegistration::new("lsp", "hover", Intent::ReadOnly, true)?;
        reg.register(a.clone())?;
        // identical re-registration is a no-op
        reg.register(a)?;
        // different intent for the same (plugin, op) conflicts
        let b = PluginOpRegistration::new("lsp", "hover", Intent::WriteAllowed, true)?;
        assert!(matches!(reg.register(b), Err(PluginError::Conflict(_))));
        Ok(())
    }

    #[test]
    fn flush_drains_only_the_named_plugin() -> TestResult {
        let mut reg = OpRegistry::new();
        reg.register(PluginOpRegistration::new(
            "lsp",
            "hover",
            Intent::ReadOnly,
            true,
        )?)?;
        reg.register(PluginOpRegistration::new(
            "fmt",
            "run",
            Intent::WriteAllowed,
            true,
        )?)?;
        let flushed = reg.flush("lsp");
        assert_eq!(flushed, vec!["plugin.lsp.hover".to_owned()]);
        assert_eq!(reg.pending(None).len(), 1);
        Ok(())
    }
}
