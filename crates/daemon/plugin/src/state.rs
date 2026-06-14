use std::sync::{Mutex, MutexGuard};

use config::configs::daemon::PluginRuntimeConfig;

use crate::pyright_lsp::PyrightLspRuntime;
use crate::PluginRuntimeError;

/// Instance-owned static plugin provider runtime.
pub struct PluginRuntime {
    pub(super) config: PluginRuntimeConfig,
    pyright_lsp: Mutex<PyrightLspRuntime>,
}

impl PluginRuntime {
    /// Build a plugin runtime over its typed config.
    #[must_use]
    pub fn new(config: PluginRuntimeConfig) -> Self {
        Self {
            pyright_lsp: Mutex::new(PyrightLspRuntime::new(&config.pyright_lsp)),
            config,
        }
    }

    pub(super) fn lock_pyright_lsp(
        &self,
    ) -> Result<MutexGuard<'_, PyrightLspRuntime>, PluginRuntimeError> {
        self.pyright_lsp
            .lock()
            .map_err(|_| PluginRuntimeError::StateLockPoisoned("pyright_lsp runtime"))
    }
}
