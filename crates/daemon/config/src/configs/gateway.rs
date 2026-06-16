//! Typed schema for host gateway defaults in `eos-sandbox/config/prd.yml`.

use anyhow::{Context, Result};
use serde::Deserialize;

use crate::ConfigDocument;

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GatewayConfig {
    pub default_image_profile: GatewayImageProfileConfig,
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GatewayImageProfileConfig {
    pub image: String,
    pub platform: Option<String>,
}

impl GatewayConfig {
    /// Deserialize the `gateway` section from a generic config document.
    ///
    /// # Errors
    /// Returns an error if the section is missing, malformed, or semantically
    /// invalid.
    pub fn from_document(doc: &ConfigDocument) -> Result<Self> {
        let config = doc
            .section::<Self>("gateway")
            .context("deserialize gateway config section")?;
        config.validate()?;
        Ok(config)
    }

    /// Validate semantic constraints beyond the YAML shape.
    ///
    /// # Errors
    /// Returns an error if required strings are empty.
    pub fn validate(&self) -> Result<()> {
        require_non_empty(
            &self.default_image_profile.image,
            "gateway.default_image_profile.image",
        )
    }
}

fn require_non_empty(value: &str, field: &str) -> Result<()> {
    if value.trim().is_empty() {
        anyhow::bail!("{field} must be non-empty");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn prd_gateway_section_deserializes_and_validates() {
        let doc = crate::load_prd().expect("prd config loads");

        GatewayConfig::from_document(&doc).expect("gateway section deserializes");
    }
}
