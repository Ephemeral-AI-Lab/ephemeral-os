use anyhow::{bail, Result};
use serde_json::{json, Value};

use crate::container::docker;

use super::args::{optional_string_arg, required_string_arg};
use super::docker_json::parse_json_lines;
use super::SandboxHost;

impl SandboxHost {
    pub fn image_profiles_list(&self, _args: &Value) -> Result<Value> {
        Ok(json!({
            "profiles": [{
                "name": "default",
                "image": self.config.image.clone(),
                "platform": self.config.platform.clone(),
                "default": true,
            }]
        }))
    }

    pub fn image_list(&self, _args: &Value) -> Result<Value> {
        docker(["image", "ls", "--format", "{{json .}}"])
            .and_then(|out| parse_json_lines(&out).map(|images| json!({"images": images})))
    }

    pub fn image_pull(&self, args: &Value) -> Result<Value> {
        let image = required_string_arg(args, "image")?;
        self.ensure_operator_image_allowed(image)?;
        let platform = optional_string_arg(args, "platform").or(self.config.platform.as_deref());
        let mut pull = vec!["pull".to_owned()];
        if let Some(platform) = platform {
            pull.extend(["--platform".to_owned(), platform.to_owned()]);
        }
        pull.push(image.to_owned());
        let output = docker(pull)?;
        Ok(json!({
            "image": image,
            "platform": platform,
            "pulled": true,
            "output": output,
        }))
    }

    fn ensure_operator_image_allowed(&self, image: &str) -> Result<()> {
        if image == self.config.image {
            return Ok(());
        }
        bail!("image {image:?} is not approved by host policy")
    }
}
