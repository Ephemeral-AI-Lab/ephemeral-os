use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MutationSource {
    ApiWrite,
    ApiEdit,
    IsolatedWorkspace,
    OverlayCapture,
    PluginOverlay,
}

impl MutationSource {
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::ApiWrite => "api_write",
            Self::ApiEdit => "api_edit",
            Self::IsolatedWorkspace => "isolated_workspace",
            Self::OverlayCapture => "overlay_capture",
            Self::PluginOverlay => "plugin_overlay",
        }
    }
}
