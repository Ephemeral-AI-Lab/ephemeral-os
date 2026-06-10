//! Every built-in daemon op is actually wire-routed (end-to-end registration),
//! under BOTH its canonical `sandbox.*` name and each legacy alias.
//!
//! Complements the in-process `registry` unit test by proving the live `eosd`
//! serves each catalog spelling over TCP: a registered handler returns success
//! OR a non-`unknown_op` error (e.g. a missing-arg `invalid_envelope`), whereas
//! an unregistered string returns `unknown_op`.

use anyhow::Result;
use eos_daemon::wire::ops::{BuiltinDaemonOp, BUILTIN_DAEMON_OP_SPECS};
use eos_e2e_test::client::error_kind;
use serde_json::json;

use crate::support::live_pool_or_skip;

/// State-toggling ops are skipped: called with injected args they would mutate
/// the lease (enter isolated mode, reset the audit floor) and perturb the loop.
/// Their dispatch is proven by the dedicated tier tests instead.
const SKIP: &[BuiltinDaemonOp] = &[
    BuiltinDaemonOp::IsolatedWorkspaceEnter,
    BuiltinDaemonOp::IsolatedWorkspaceExit,
    BuiltinDaemonOp::IsolatedWorkspaceTestReset,
    BuiltinDaemonOp::AuditResetFloor,
    // Would cancel + discard every workspace run in the shared lease.
    BuiltinDaemonOp::CancelWorkspaceRuns,
];

#[test]
fn every_builtin_op_is_wire_routed_under_both_spellings() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    for spec in BUILTIN_DAEMON_OP_SPECS {
        if SKIP.contains(&spec.op) {
            continue;
        }
        for spelling in std::iter::once(&spec.name).chain(spec.aliases) {
            let resp = lease.call(spelling, json!({}))?;
            assert_ne!(
                error_kind(&resp),
                Some("unknown_op"),
                "catalog spelling {spelling} must be registered over the wire: {resp}"
            );
        }
    }
    // Negative control: an unregistered op must surface unknown_op.
    let bogus = lease.call("api.totally.bogus.op", json!({}))?;
    assert_eq!(
        error_kind(&bogus),
        Some("unknown_op"),
        "an unregistered op must surface unknown_op: {bogus}"
    );
    Ok(())
}
