#[path = "../support/mod.rs"]
mod support;

use anyhow::Result;
use protocol::catalog;
use serde_json::json;

use support::{envelope_error_kind, envelope_status, live_pool_or_skip};

const E2E_CONFIG: &str = "crates/e2e-test/tests/plugin-disabled/config/default.test.yml";

#[test]
fn pyright_lsp_rejects_when_disabled() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let response = lease.call(
        catalog::SANDBOX_PLUGIN_PYRIGHT_LSP_QUERY_SYMBOLS,
        json!({ "file_path": "pkg/missing.py" }),
    )?;
    assert_eq!(envelope_status(&response)?, "rejected", "{response}");
    assert_eq!(
        envelope_error_kind(&response)?,
        "plugin_disabled",
        "{response}"
    );
    Ok(())
}
