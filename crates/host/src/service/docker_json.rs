use std::sync::Arc;

use anyhow::{Context, Result};
use serde_json::{json, Value};

use super::registry::SandboxRecord;

pub(crate) fn parse_json_lines(output: &str) -> Result<Vec<Value>> {
    output
        .lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| {
            serde_json::from_str(line).with_context(|| format!("parse docker JSON: {line}"))
        })
        .collect()
}

pub(crate) fn mark_managed_containers(containers: &mut [Value], records: &[Arc<SandboxRecord>]) {
    for container in containers {
        let names = container
            .get("Names")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .trim_start_matches('/');
        if let Some(record) = records
            .iter()
            .find(|record| record.container == names || record.sandbox_id == names)
        {
            container["managed"] = json!(true);
            container["sandbox_id"] = json!(record.sandbox_id.clone());
        } else {
            container["managed"] = json!(false);
        }
    }
}
