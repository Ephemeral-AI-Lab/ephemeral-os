#![allow(dead_code)]

use std::sync::Arc;

use anyhow::{Context, Result};
use eos_e2e_test::{live_pool_with_config, NodePool};
use serde_json::Value;

pub fn live_pool_or_skip() -> Result<Option<Arc<NodePool>>> {
    let Some(pool) = live_pool_with_config(crate::E2E_CONFIG)? else {
        eprintln!("skipping live eos-e2e-test; enable with `--features e2e`");
        return Ok(None);
    };
    Ok(Some(pool))
}

pub fn as_bool(value: &Value, key: &str) -> Result<bool> {
    value
        .get(key)
        .and_then(Value::as_bool)
        .with_context(|| format!("{key} missing or not bool in {value}"))
}

pub fn as_i64(value: &Value, key: &str) -> Result<i64> {
    value
        .get(key)
        .and_then(Value::as_i64)
        .with_context(|| format!("{key} missing or not i64 in {value}"))
}

pub fn as_str<'a>(value: &'a Value, key: &str) -> Result<&'a str> {
    value
        .get(key)
        .and_then(Value::as_str)
        .with_context(|| format!("{key} missing or not string in {value}"))
}

pub fn array<'a>(value: &'a Value, key: &str) -> Result<&'a Vec<Value>> {
    value
        .get(key)
        .and_then(Value::as_array)
        .with_context(|| format!("{key} missing or not array in {value}"))
}

pub fn stdout(value: &Value) -> &str {
    value
        .get("output")
        .and_then(|output| output.get("stdout"))
        .and_then(Value::as_str)
        .or_else(|| value.get("stdout").and_then(Value::as_str))
        .unwrap_or_default()
}

pub fn conflict_reason(value: &Value) -> String {
    value
        .get("conflict")
        .and_then(|conflict| conflict.get("reason"))
        .and_then(Value::as_str)
        .or_else(|| value.get("conflict_reason").and_then(Value::as_str))
        .unwrap_or_default()
        .to_owned()
}

pub fn conflict_message(value: &Value) -> String {
    value
        .get("conflict")
        .and_then(|conflict| conflict.get("message"))
        .and_then(Value::as_str)
        .or_else(|| value.get("conflict_reason").and_then(Value::as_str))
        .unwrap_or_default()
        .to_owned()
}
