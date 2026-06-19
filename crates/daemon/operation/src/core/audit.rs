use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MutationSource {
    DirectWrite,
    DirectEdit,
    IsolatedNetwork,
    OverlayCapture,
    PluginOverlay,
}

impl MutationSource {
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::DirectWrite => "direct_write",
            Self::DirectEdit => "direct_edit",
            Self::IsolatedNetwork => "isolated",
            Self::OverlayCapture => "overlay_capture",
            Self::PluginOverlay => "plugin_overlay",
        }
    }
}
