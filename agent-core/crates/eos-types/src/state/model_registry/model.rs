//! `ModelRegistration` DTO (Rust `db/models/model_registration.py`).
//!
//! `class_path` survives **only as migration data** (anchor §2 non-goal): final
//! dispatch is typed by `llm_provider` + `model_key` downstream. This DTO keeps
//! the raw migration columns and carries no dispatch logic (GC-state-04).

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::UtcDateTime;

/// Immutable view of a persisted model registration (Rust
/// `ModelRegistrationRecord`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct ModelRegistration {
    /// Autoincrement primary key.
    pub id: i64,
    /// Normalized model key (DB column `key`; mapped in `eos-db`, anchor §4).
    pub model_key: String,
    /// Human-readable label.
    pub label: String,
    /// Migration-only import path; never used for dispatch (GC-state-04).
    pub class_path: String,
    /// Opaque JSON kwargs string; parsing/redaction/env-resolution is `eos-db`'s.
    pub kwargs_json: String,
    /// Whether this is the single active registration.
    pub is_active: bool,
    /// Creation timestamp.
    pub created_at: UtcDateTime,
    /// Last-update timestamp.
    pub updated_at: UtcDateTime,
}
