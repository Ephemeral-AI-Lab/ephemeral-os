//! eos-config — typed runtime configuration, loaded from files only.
//!
//! This crate is a generic config loader. [`load`] reads the committed
//! `agent-core/config/prd.yml` baseline merged with a gitignored
//! `agent-core/config/local.yml` override (objects recurse, scalars/arrays
//! replace) into a [`ConfigDocument`]; each owning crate deserializes its
//! top-level section via [`ConfigDocument::section`] and enforces its own ranges
//! with the section type's `validate()`. There is no environment-variable or CLI
//! config selection: config is chosen by file. It is a leaf of the dependency
//! DAG — no internal upstream edge (not even `eos-types`) — consumed read-only.
//!
//! It deliberately does **not** aggregate sections into a central composition
//! struct (there is no `CentralConfig`), resolve the active model (that is
//! `eos-db`), hold secrets (those live only in the gitignored override), open
//! connections, or spawn tasks.
//!
//! The section schemas ([`DatabaseConfig`], [`ProvidersConfig`],
//! [`AttemptConfig`], …) live here for now; they migrate to their owning crates'
//! `config.rs` as those crates stabilize.
#![forbid(unsafe_code)]
#![warn(missing_docs)]

mod attempt;
mod database;
mod document;
mod error;
mod loader;
mod markdown;
mod providers;

pub use attempt::AttemptConfig;
pub use database::{DatabaseConfig, DatabaseUrl, DEFAULT_SQLITE_DATABASE_URL};
pub use document::ConfigDocument;
pub use error::ConfigError;
pub use loader::{load, load_with_override};
pub use markdown::parse_markdown_frontmatter;
pub use providers::{ProvidersConfig, RetryConfig};
