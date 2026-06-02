//! The single `ConfigError` enum for this crate (spec-conventions §8).

/// Errors raised while loading, parsing, or validating [`CentralConfig`].
///
/// [`CentralConfig`]: crate::CentralConfig
#[derive(Debug, thiserror::Error)]
#[non_exhaustive]
pub enum ConfigError {
    /// A network database url (a `postgres`/`mysql` scheme, or a credentialed
    /// `//host` authority) was supplied; agent-core is sqlite-only and rejects
    /// it (spec-conventions §2 — no `PostgreSQL`).
    #[error("network database urls are not supported in agent-core: {0}")]
    NetworkDatabaseUrl(String),
    /// A database url that is neither a `sqlite:` scheme nor a local `.db` path.
    #[error("unsupported database url (expected local sqlite): {0}")]
    UnsupportedDatabaseUrl(String),
    /// The docker section set both `privileged` and `no_privilege`.
    #[error("docker config sets both privileged and no_privilege")]
    DockerPrivilegeContradiction,
    /// A numeric field fell outside its allowed range (Pydantic `ge`/`gt` parity).
    #[error("config value '{field}' is out of range: {detail}")]
    OutOfRange {
        /// Dotted config path of the offending field (e.g. `database.pool_size`).
        field: String,
        /// The range constraint that was violated (e.g. `must be >= 1`).
        detail: String,
    },
    /// A config file could not be read from disk.
    #[error("failed to read config file")]
    ReadFile(#[source] std::io::Error),
    /// The config yaml — a file, an env value, or the merged tree — failed to
    /// parse or to deserialize into [`CentralConfig`] (this is where
    /// `deny_unknown_fields` and the [`DatabaseUrl`] parse surface).
    ///
    /// [`CentralConfig`]: crate::CentralConfig
    /// [`DatabaseUrl`]: crate::DatabaseUrl
    #[error("failed to parse config yaml")]
    ParseYaml(#[source] serde_yaml::Error),
}
