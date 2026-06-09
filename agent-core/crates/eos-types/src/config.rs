//! Shared config error type used by owner-local config structs and the runtime
//! file loader.

/// Errors raised while loading, parsing, or deserializing config.
#[derive(Debug, thiserror::Error)]
#[non_exhaustive]
pub enum ConfigError {
    /// A requested top-level config section was absent from the document.
    #[error("config section '{section}' is missing")]
    MissingSection {
        /// The missing top-level section name.
        section: String,
    },
    /// The config document root was not a YAML mapping.
    #[error("config document root must be a YAML mapping")]
    InvalidDocumentRoot,
    /// A network database url (a `postgres`/`mysql` scheme, or a credentialed
    /// `//host` authority) was supplied; agent-core is sqlite-only and rejects it.
    #[error("network database urls are not supported in agent-core: {0}")]
    NetworkDatabaseUrl(String),
    /// A database url that is neither a `sqlite:` scheme nor a local `.db` path.
    #[error("unsupported database url (expected local sqlite): {0}")]
    UnsupportedDatabaseUrl(String),
    /// A numeric field fell outside its allowed range.
    #[error("config value '{field}' is out of range: {detail}")]
    OutOfRange {
        /// Dotted config path of the offending field (e.g. `database.pool_size`).
        field: String,
        /// The range constraint that was violated (e.g. `must be >= 1`).
        detail: String,
    },
    /// A required config value is absent or empty.
    #[error("config value '{field}' is required")]
    MissingValue {
        /// Dotted config path of the missing value.
        field: String,
    },
    /// A config file could not be read from disk.
    #[error("failed to read config file")]
    ReadFile(#[source] std::io::Error),
    /// A config file or a deserialized section failed to parse.
    #[error("failed to parse config yaml")]
    ParseYaml(#[source] serde_yaml::Error),
}
