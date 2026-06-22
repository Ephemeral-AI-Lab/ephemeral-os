//! Config surface for the host-side sandbox manager.
//!
//! The manager currently has no persistent YAML-backed fields; this value object
//! keeps the manager config boundary explicit as the shared sandbox config crate
//! grows.

use crate::configs::validate::ConfigFieldError;

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct ManagerConfig;

impl ManagerConfig {
    /// Validate manager config invariants.
    ///
    /// # Errors
    /// The current manager config has no fields, so validation always succeeds.
    pub fn validate(&self) -> Result<(), ConfigFieldError> {
        Ok(())
    }
}
