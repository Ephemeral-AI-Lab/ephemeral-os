//! Sqlite-only database configuration and the validated [`DatabaseUrl`] newtype.
//!
//! Network database backends are rejected at parse time (GC-eos-config-02);
//! `pool_pre_ping`/`max_overflow` from the Python `DatabaseConfig` are dropped as
//! connection-server concepts meaningless for embedded sqlite (GC-eos-config-03).

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::error::ConfigError;

/// The local sqlite database url default (`sections/database.py:14`).
pub const DEFAULT_SQLITE_DATABASE_URL: &str = "sqlite:///./.ephemeralos/ephemeralos.db";

/// A validated local-sqlite database url. Network backends are rejected at parse
/// time (`api-parse-dont-validate`, `type-no-stringly`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, JsonSchema)]
#[serde(transparent)] // serialize as a plain string, not a 1-tuple
#[schemars(transparent)] // render as a plain string in the JSON Schema (matches Python `str`)
pub struct DatabaseUrl(String);

impl DatabaseUrl {
    /// Parse `raw` into a validated url, rejecting network databases.
    ///
    /// # Errors
    /// Returns [`ConfigError::NetworkDatabaseUrl`] for a `postgres`/`mysql`
    /// scheme or a credentialed `//host` authority, and
    /// [`ConfigError::UnsupportedDatabaseUrl`] for anything that is neither a
    /// `sqlite:` scheme nor a local `.db` path.
    pub fn parse(raw: impl Into<String>) -> Result<Self, ConfigError> {
        let raw = raw.into();
        let lower = raw.to_ascii_lowercase();
        // Reject network DBs (fail fast) — non-goal: no PostgreSQL in agent-core.
        let network_scheme = lower.starts_with("postgres://")
            || lower.starts_with("postgresql://")
            || lower.starts_with("mysql://");
        // `@` only counts as a credentialed network authority when it appears in
        // the `//host` segment, so a local sqlite path with `@` in a directory
        // name (e.g. `sqlite:///home/user@host/db.db`) is not false-rejected.
        let credentialed_authority = lower
            .split_once("//")
            .is_some_and(|(_, rest)| rest.split('/').next().is_some_and(|a| a.contains('@')));
        if network_scheme || credentialed_authority {
            return Err(ConfigError::NetworkDatabaseUrl(raw));
        }
        if !(lower.starts_with("sqlite:") || raw.ends_with(".db")) {
            return Err(ConfigError::UnsupportedDatabaseUrl(raw));
        }
        Ok(Self(raw))
    }

    /// The validated url string.
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl<'de> Deserialize<'de> for DatabaseUrl {
    fn deserialize<D: serde::Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        Self::parse(String::deserialize(d)?).map_err(serde::de::Error::custom)
    }
}

/// Sqlite-only database configuration. The new `busy_timeout_ms`/`wal`/
/// `foreign_keys` controls are the agent-core sqlite tunables (plan §4).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
#[non_exhaustive]
pub struct DatabaseConfig {
    /// Database url; rejects network backends at parse time.
    pub url: DatabaseUrl,
    /// Connection-pool size. Range-checked `>= 1` during validation.
    pub pool_size: u32,
    /// Sqlite `busy_timeout` in milliseconds.
    pub busy_timeout_ms: u64,
    /// Enable write-ahead logging (`PRAGMA journal_mode=WAL`).
    pub wal: bool,
    /// Enable `PRAGMA foreign_keys`.
    pub foreign_keys: bool,
    /// Echo executed sql statements.
    pub echo: bool,
}

impl Default for DatabaseConfig {
    fn default() -> Self {
        Self {
            url: DatabaseUrl(DEFAULT_SQLITE_DATABASE_URL.to_owned()),
            pool_size: 5,
            busy_timeout_ms: 5000,
            wal: true,
            foreign_keys: true,
            echo: false,
        }
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // unwrap permitted in tests (err-no-unwrap-prod)
    use super::*;

    // AC-eos-config-06: network DB urls are rejected; local sqlite parses.
    #[test]
    fn test_network_database_url_rejected() {
        assert!(matches!(
            DatabaseUrl::parse("postgresql://user:pw@db.example.com/app"),
            Err(ConfigError::NetworkDatabaseUrl(_))
        ));
        assert!(matches!(
            DatabaseUrl::parse("postgres://db/app"),
            Err(ConfigError::NetworkDatabaseUrl(_))
        ));
        assert!(matches!(
            DatabaseUrl::parse("mysql://host/app"),
            Err(ConfigError::NetworkDatabaseUrl(_))
        ));
        // local sqlite parses
        assert_eq!(
            DatabaseUrl::parse("sqlite:///./x.db").unwrap().as_str(),
            "sqlite:///./x.db"
        );
        // a bare `.db` path is accepted
        assert!(DatabaseUrl::parse("./local.db").is_ok());
        // an `@` inside a local sqlite path is NOT a credentialed authority
        assert!(DatabaseUrl::parse("sqlite:///home/user@host/db.db").is_ok());
        // neither sqlite nor `.db` → unsupported
        assert!(matches!(
            DatabaseUrl::parse("redis://localhost"),
            Err(ConfigError::UnsupportedDatabaseUrl(_))
        ));
    }

    // AC-eos-config-02 (database subset): defaults match the Python source.
    #[test]
    fn test_database_defaults() {
        let d = DatabaseConfig::default();
        assert_eq!(d.url.as_str(), DEFAULT_SQLITE_DATABASE_URL);
        assert_eq!(d.pool_size, 5);
        assert!(d.wal);
        assert!(d.foreign_keys);
        assert!(!d.echo);
    }
}
