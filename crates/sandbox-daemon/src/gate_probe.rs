//! Live-remount kernel gate-probe subcommand adapter.
//!
//! `<binary> gate-probe <scratch_root>` runs the same-upperdir staged-switch +
//! userxattr parity probe in this fresh (single-threaded) process — the
//! daemon spawns it at boot because `unshare(CLONE_NEWUSER)` needs a
//! single-threaded caller. Exit `0` means the gate holds and live remount is
//! enabled; any non-zero exit keeps squash commit-only.

use std::path::PathBuf;

use anyhow::{anyhow, Result};

pub(crate) fn run(mut args: std::env::Args) -> Result<()> {
    let scratch_root = args
        .next()
        .map(PathBuf::from)
        .ok_or_else(|| anyhow!("gate-probe requires a scratch_root argument"))?;
    if sandbox_runtime_namespace_process::gate::run_gate_probe(&scratch_root) {
        Ok(())
    } else {
        std::process::exit(1);
    }
}
