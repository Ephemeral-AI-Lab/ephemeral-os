//! The three plugin dispatch modes, selected from `(intent, auto_workspace_overlay)`.
//!
//! # MF-1 — ONE writer per `layer_stack_root` (STATE LOUDLY)
//!
//! Both the WRITE_ALLOWED path AND the self-managed callback path publish through
//! the SAME per-`layer_stack_root` single OCC writer + storage lease. That writer
//! is the existing [`eos_ephemeral::OccRuntimeServicesPort`] (keyed on root so
//! every publish routes through the ONE `occ-commit-queue` writer — MF-1). This
//! crate CONSUMES that port; it NEVER defines a parallel OCC services trait and
//! NEVER links `eos-occ`. A second writer instance for the self-managed callback
//! would silently corrupt the linearization point — so the self-managed mode is
//! generic over the very same [`eos_ephemeral::OccRuntimeServicesPort`] value the
//! primary path uses.
//!
//! # The three modes
//!
//! * [`DispatchMode::ReadOnlyWarmServer`] — `Intent::ReadOnly`: the op runs
//!   OUT-OF-PROCESS in the warm per-session server (no per-call overlay, no ns
//!   child, no publish). Replaces the importlib in-process call.
//! * [`DispatchMode::WriteAllowedEosdOwned`] — `Intent::WriteAllowed` +
//!   `auto_workspace_overlay = true`: `eosd` owns the per-op overlay + the OCC
//!   publish around the warm-server call (the canonical wrapper).
//! * [`DispatchMode::SelfManagedCallback`] — `auto_workspace_overlay = false`:
//!   the plugin manages its own overlay and calls BACK over the PPC channel to
//!   commit; that callback MUST route through the SAME single-writer port (MF-1),
//!   leaving the existing publish path UNCHANGED.
//!
//! Isolated mode blocks ALL plugin dispatch (`ForbiddenInIsolatedWorkspace`).
//!
//! `// PORT backend/src/sandbox/ephemeral_workspace/plugin/op_registry.py:14-23 — intent -> runner`
//! `// PORT backend/src/sandbox/ephemeral_workspace/plugin/op_registry.py:224-237 — _dispatch_runner_for_entry`

use eos_ephemeral::OccRuntimeServicesPort;
use eos_layerstack::SnapshotLeasePort;
use eos_protocol::Intent;

use crate::error::{PluginError, Result};
use crate::ppc::PpcEnvelope;
use crate::registry::PluginOpRegistration;
use crate::warm_server::WarmServer;

/// The dispatch runner a flushed registration resolves to.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[non_exhaustive]
pub enum DispatchMode {
    /// `Intent::ReadOnly` -> warm server, out-of-process, no overlay/publish.
    ReadOnlyWarmServer,
    /// `Intent::WriteAllowed` + `auto_workspace_overlay` -> eosd owns overlay+OCC.
    WriteAllowedEosdOwned,
    /// `auto_workspace_overlay = false` -> plugin self-manages; bidirectional
    /// OCC commit callback over PPC (same single writer — MF-1).
    SelfManagedCallback,
}

impl DispatchMode {
    /// Pick the dispatch mode for a registration from intent + the opt-out flag.
    ///
    /// READ_ONLY wins FIRST: a read never publishes, so `auto_workspace_overlay`
    /// is a no-op for it (Python `_dispatch_runner_for_entry` returns `None` ->
    /// in-process for any READ_ONLY entry). Self-managed (`auto=false`) only
    /// matters for write-capable ops, which own their overlay+OCC and call back.
    /// `// PORT backend/src/sandbox/ephemeral_workspace/plugin/op_registry.py:224-237 — _dispatch_runner_for_entry`
    pub fn for_registration(registration: &PluginOpRegistration) -> Self {
        match registration.intent {
            // PORT op_registry.py:236-237 — READ_ONLY runs in-process (here: warm
            //   server) regardless of the flag; reads never publish.
            Intent::ReadOnly => Self::ReadOnlyWarmServer,
            // PORT op_registry.py:226 — auto_workspace_overlay=False: plugin owns
            //   its overlay+OCC; skip the standard wrapper (publish path UNCHANGED).
            _ if !registration.auto_workspace_overlay => Self::SelfManagedCallback,
            // PORT op_registry.py:230-235 — WRITE_ALLOWED runs the overlay+OCC wrapper.
            Intent::WriteAllowed => Self::WriteAllowedEosdOwned,
            // LIFECYCLE never reaches dispatch (rejected at registration).
            Intent::Lifecycle => Self::ReadOnlyWarmServer,
        }
    }
}

/// Guard: refuse plugin dispatch when an isolated workspace is active for the
/// caller. Isolated mode blocks plugin/LSP ops entirely.
/// `// PORT backend/src/sandbox/ephemeral_workspace/plugin/op_registry.py:14-23 — isolated-mode blocks plugin ops`
pub fn ensure_not_isolated(isolated_active: bool) -> Result<()> {
    if isolated_active {
        return Err(PluginError::ForbiddenInIsolatedWorkspace);
    }
    Ok(())
}

/// READ_ONLY: run the op in the warm per-session server, out-of-process. No
/// per-call overlay, no namespace child, no publish cycle — the handler queries a
/// long-lived `PluginService` over the [PPC channel](crate::ppc).
/// `// PORT backend/src/sandbox/ephemeral_workspace/plugin/op_registry.py:16-19 — READ_ONLY in-process`
/// `// PORT backend/src/sandbox/ephemeral_workspace/plugin/overlay_child.py:129 — importlib -> warm-server PPC call`
pub fn dispatch_read_only(_server: &mut WarmServer, _request: PpcEnvelope) -> Result<PpcEnvelope> {
    // PORT overlay_child.py:129 — send the request frame to the warm server over
    //   PPC and await the message-id-matched reply (no overlay, no publish).
    todo!("PORT overlay_child.py:129 — READ_ONLY warm-server PPC round-trip")
}

/// WRITE_ALLOWED (auto overlay): `eosd` owns the per-op overlay + the OCC publish
/// AROUND the warm-server call. The publish goes through the injected
/// [`OccRuntimeServicesPort`] — the SAME single writer per `layer_stack_root`
/// (MF-1). Snapshot/lease comes from the [`SnapshotLeasePort`] HINGE (never occ).
/// `// PORT backend/src/sandbox/ephemeral_workspace/plugin/overlay_dispatch.py:29-91 — run_plugin_op_with_workspace_overlay`
pub fn dispatch_write_allowed<L, O>(
    _layer_stack: &mut L,
    _services: &O,
    _server: &mut WarmServer,
    _request: PpcEnvelope,
) -> Result<PpcEnvelope>
where
    L: SnapshotLeasePort,
    O: OccRuntimeServicesPort,
{
    // PORT overlay_dispatch.py:57-60 — acquire per-op overlay over the leased snapshot.
    // PORT overlay_dispatch.py:64-71 — run the op in the warm server over PPC.
    // PORT overlay_dispatch.py:72-88 — publish_cycle through the ONE OCC writer
    //   (services.apply_changeset), then release the lease.
    todo!("PORT overlay_dispatch.py:29-91 — eosd-owned overlay + single-writer OCC publish around the warm-server call")
}

/// Self-managed (`auto_workspace_overlay = false`): the plugin owns its overlay
/// and calls BACK over the PPC channel to commit. MF-1: that callback MUST route
/// through the SAME injected [`OccRuntimeServicesPort`] (the ONE per-root
/// `occ-commit-queue` writer + storage lease) — NOT a second writer instance —
/// keeping the existing publish path UNCHANGED.
/// `// PORT backend/src/sandbox/ephemeral_workspace/plugin/op_registry.py:226-229 — auto_workspace_overlay=False self-managed branch`
pub fn dispatch_self_managed<O>(
    _services: &O,
    _server: &mut WarmServer,
    _request: PpcEnvelope,
) -> Result<PpcEnvelope>
where
    O: OccRuntimeServicesPort,
{
    // PORT op_registry.py:226-229 — run the op in the warm server; service its
    //   OCC commit callback on the SAME PPC channel, routing the changeset through
    //   `services.apply_changeset` (MF-1: the same single writer the primary path
    //   uses — never a second writer instance).
    todo!("PORT op_registry.py:226-229 — self-managed op with OCC commit callback routed through the SAME single writer (MF-1)")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mode_selection_matches_intent_and_flag() {
        let ro = PluginOpRegistration::new("lsp", "hover", Intent::ReadOnly, true).unwrap();
        assert_eq!(
            DispatchMode::for_registration(&ro),
            DispatchMode::ReadOnlyWarmServer
        );
        let wa = PluginOpRegistration::new("fmt", "run", Intent::WriteAllowed, true).unwrap();
        assert_eq!(
            DispatchMode::for_registration(&wa),
            DispatchMode::WriteAllowedEosdOwned
        );
        // auto_workspace_overlay=false self-manages ONLY for write-capable ops
        // (the LSP apply.py path): plugin owns overlay+OCC, calls back to commit.
        let sm = PluginOpRegistration::new("lsp", "apply", Intent::WriteAllowed, false).unwrap();
        assert_eq!(
            DispatchMode::for_registration(&sm),
            DispatchMode::SelfManagedCallback
        );
        // READ_ONLY ignores the flag — a read never publishes, so auto=false
        // still runs in the warm server, NOT through an OCC commit callback.
        let ro_no_overlay =
            PluginOpRegistration::new("lsp", "hover", Intent::ReadOnly, false).unwrap();
        assert_eq!(
            DispatchMode::for_registration(&ro_no_overlay),
            DispatchMode::ReadOnlyWarmServer
        );
    }

    #[test]
    fn isolated_mode_blocks_plugin_ops() {
        assert!(matches!(
            ensure_not_isolated(true),
            Err(PluginError::ForbiddenInIsolatedWorkspace)
        ));
        assert!(ensure_not_isolated(false).is_ok());
    }
}
