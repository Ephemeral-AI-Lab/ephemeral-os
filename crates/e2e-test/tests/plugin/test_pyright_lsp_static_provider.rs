use anyhow::{Context, Result};
use protocol::catalog;
use serde_json::{json, Value};

use crate::support::{as_bool, as_str, live_pool_or_skip};

#[test]
fn pyright_lsp_setup_and_health() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;

    let list = lease.call_ok(catalog::SANDBOX_PLUGIN_LIST, json!({}))?;
    let listed_provider = provider(&list)?;
    assert_eq!(
        as_str(listed_provider, "provider")?,
        "pyright_lsp",
        "{list}"
    );
    assert!(as_bool(listed_provider, "enabled")?, "{list}");

    let health = lease.call_ok(catalog::SANDBOX_PLUGIN_HEALTH, json!({}))?;
    let health_provider = provider(&health)?;
    assert_eq!(
        as_str(health_provider, "provider")?,
        "pyright_lsp",
        "{health}"
    );
    assert!(as_bool(health_provider, "enabled")?, "{health}");
    assert!(as_bool(health_provider, "running")?, "{health}");
    assert!(as_bool(health_provider, "initialize_success")?, "{health}");
    assert!(
        health_provider
            .get("process_id")
            .and_then(Value::as_u64)
            .is_some(),
        "health should include a real process id: {health}"
    );
    assert!(
        health_provider
            .get("active_manifest_key")
            .and_then(Value::as_str)
            .is_some_and(|value| value.starts_with("version:")),
        "health should report active manifest key: {health}"
    );
    assert!(
        health_provider
            .get("projection_root")
            .and_then(Value::as_str)
            .is_some_and(|value| value.contains("pyright_lsp")),
        "health should include projection root: {health}"
    );
    assert!(
        health_provider
            .get("capabilities")
            .is_some_and(Value::is_object),
        "health should include initialize capabilities: {health}"
    );
    Ok(())
}

#[test]
fn pyright_lsp_query_symbols() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    write_py(
        &lease,
        "pkg/symbols.py",
        "class PhaseSeven:\n    pass\n\ndef live_symbol(value: int) -> int:\n    return value\n",
    )?;

    let response = lease.call_ok(
        catalog::SANDBOX_PLUGIN_PYRIGHT_LSP_QUERY_SYMBOLS,
        json!({
            "file_path": "pkg/symbols.py",
            "query": "live_symbol",
            "workspace": false
        }),
    )?;
    assert_pyright_fresh(&response)?;
    let symbols = response["symbols"]
        .as_array()
        .context("symbols missing from response")?;
    assert!(
        symbols.iter().any(|symbol| symbol["name"] == "live_symbol"),
        "symbol response should include live_symbol: {response}"
    );
    Ok(())
}

#[test]
fn pyright_lsp_definition_and_references() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    seed_navigation_workspace(&lease, "pkg/lib.py")?;

    let definition = lease.call_ok(
        catalog::SANDBOX_PLUGIN_PYRIGHT_LSP_DEFINITION,
        json!({
            "file_path": "pkg/main.py",
            "position": { "line": 2, "character": 9 }
        }),
    )?;
    assert_location_file(&definition, "pkg/lib.py")?;

    let references = lease.call_ok(
        catalog::SANDBOX_PLUGIN_PYRIGHT_LSP_REFERENCES,
        json!({
            "file_path": "pkg/lib.py",
            "position": { "line": 0, "character": 4 },
            "include_declaration": true
        }),
    )?;
    assert_location_file(&references, "pkg/main.py")?;
    Ok(())
}

#[test]
fn pyright_lsp_navigation_refreshes_after_update() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    seed_navigation_workspace(&lease, "pkg/lib.py")?;

    let before = lease.call_ok(
        catalog::SANDBOX_PLUGIN_PYRIGHT_LSP_DEFINITION,
        json!({
            "file_path": "pkg/main.py",
            "position": { "line": 2, "character": 9 }
        }),
    )?;
    assert_location_file(&before, "pkg/lib.py")?;

    write_py(
        &lease,
        "pkg/alt.py",
        "def target(value: int) -> int:\n    return value + 2\n",
    )?;
    write_py(
        &lease,
        "pkg/main.py",
        "from pkg.alt import target\n\nanswer = target(41)\nagain = target(1)\n",
    )?;
    let after = lease.call_ok(
        catalog::SANDBOX_PLUGIN_PYRIGHT_LSP_DEFINITION,
        json!({
            "file_path": "pkg/main.py",
            "position": { "line": 2, "character": 9 }
        }),
    )?;
    assert_location_file(&after, "pkg/alt.py")?;
    assert!(
        !locations(&after)?
            .iter()
            .any(|location| location["file_path"] == "pkg/lib.py"),
        "definition should not return old target after refresh: {after}"
    );
    Ok(())
}

#[test]
fn pyright_lsp_diagnostics_refreshes_after_update() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    write_py(&lease, "pkg/broken.py", "def broken(:\n    pass\n")?;

    let before = lease.call_ok(
        catalog::SANDBOX_PLUGIN_PYRIGHT_LSP_DIAGNOSTICS,
        json!({ "file_path": "pkg/broken.py" }),
    )?;
    assert_pyright_fresh(&before)?;
    assert!(
        before["diagnostics"]
            .as_array()
            .is_some_and(|diagnostics| !diagnostics.is_empty()),
        "broken file should produce diagnostics: {before}"
    );

    write_py(&lease, "pkg/broken.py", "def broken() -> None:\n    pass\n")?;
    let after = lease.call_ok(
        catalog::SANDBOX_PLUGIN_PYRIGHT_LSP_DIAGNOSTICS,
        json!({ "file_path": "pkg/broken.py" }),
    )?;
    assert_pyright_fresh(&after)?;
    assert_eq!(
        after["diagnostics"].as_array().map(Vec::len),
        Some(0),
        "fixed file should clear diagnostics after refresh: {after}"
    );
    Ok(())
}

fn seed_navigation_workspace(lease: &e2e_test::NodeLease<'_>, target_file: &str) -> Result<()> {
    write_py(lease, "pkg/__init__.py", "")?;
    write_py(
        lease,
        target_file,
        "def target(value: int) -> int:\n    return value + 1\n",
    )?;
    write_py(
        lease,
        "pkg/main.py",
        "from pkg.lib import target\n\nanswer = target(41)\nagain = target(1)\n",
    )
}

fn write_py(lease: &e2e_test::NodeLease<'_>, path: &str, content: &str) -> Result<()> {
    lease.call_ok(
        catalog::SANDBOX_FILE_WRITE,
        json!({ "path": path, "content": content, "overwrite": true }),
    )?;
    Ok(())
}

fn assert_pyright_fresh(response: &Value) -> Result<()> {
    assert_eq!(as_str(response, "provider")?, "pyright_lsp", "{response}");
    assert_eq!(
        as_str(response, "freshness")?,
        "analyzer_reflected",
        "{response}"
    );
    assert!(
        !as_bool(response, "stale")?,
        "pyright response should not be stale: {response}"
    );
    assert!(
        as_str(response, "manifest_key")?.starts_with("version:"),
        "{response}"
    );
    Ok(())
}

fn assert_location_file(response: &Value, expected_file: &str) -> Result<()> {
    assert_pyright_fresh(response)?;
    assert!(
        locations(response)?
            .iter()
            .any(|location| location["file_path"] == expected_file),
        "expected location in {expected_file}: {response}"
    );
    Ok(())
}

fn provider(response: &Value) -> Result<&Value> {
    response["providers"]
        .as_array()
        .and_then(|providers| providers.first())
        .context("providers[0] missing")
}

fn locations(response: &Value) -> Result<&Vec<Value>> {
    response["locations"]
        .as_array()
        .context("locations missing from response")
}
