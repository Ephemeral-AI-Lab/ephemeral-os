//! Stats response DTOs.
//!
//! Intentionally empty in Phase 3. The `/api/stats/*` response shapes are pinned
//! by the stats queries in Phase 6 (`eos-backend-obs`), which read both
//! `backend.db` and agent-core `state_reader()` data. Defining the structs here
//! before those queries exist would risk fixing the wrong shape, so this module
//! is a placeholder until Phase 6 lands.
