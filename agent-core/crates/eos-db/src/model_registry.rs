//! `ModelRegistry` — model-registration CRUD, secret redaction, env-placeholder
//! resolution, and JSON seeding (Python `model_store.py`).
//!
//! `class_path` is carried verbatim and never used to import or dispatch
//! (anchor §2, GC-eos-db-01).

use async_trait::async_trait;
use serde_json::{Map, Value};
use sqlx::{Sqlite, SqlitePool};
use time::OffsetDateTime;

use eos_state::{CoreError, JsonObject, ModelRegistration, ModelStore, Sealed, UtcDateTime};

use crate::error::DbError;
use crate::json_col;

const SECRET_MARKERS: [&str; 6] = [
    "api_key",
    "auth_token",
    "access_token",
    "secret",
    "password",
    "authorization",
];

/// The active registration with its kwargs parsed and env-placeholders resolved
/// (Python `get_active_resolved`). Returned by [`ModelRegistry::active_resolved`].
#[derive(Debug, Clone, PartialEq)]
pub struct ResolvedModel {
    /// Normalized model key.
    pub model_key: String,
    /// Human-readable label.
    pub label: String,
    /// Migration-only import path (never dispatched on).
    pub class_path: String,
    /// Parsed kwargs with `env:` / `${VAR}` / `$VAR` placeholders resolved.
    pub kwargs: JsonObject,
    /// Whether the registration is active (always true here).
    pub is_active: bool,
}

#[derive(Debug, Clone, sqlx::FromRow)]
struct ModelRegistrationRow {
    id: i64,
    key: String,
    label: String,
    class_path: String,
    kwargs_json: String,
    is_active: bool,
    created_at: OffsetDateTime,
    updated_at: OffsetDateTime,
}

/// `SQLite`-backed model registry (concrete; not a `Store` seam).
#[derive(Debug)]
pub struct ModelRegistry {
    pool: SqlitePool,
}

impl Sealed for ModelRegistry {}

impl ModelRegistry {
    pub(crate) fn new(pool: SqlitePool) -> Self {
        Self { pool }
    }

    /// The active registration with kwargs resolved against the process env.
    ///
    /// # Errors
    /// Returns [`DbError`] on a query failure.
    pub async fn active_resolved(&self) -> Result<Option<ResolvedModel>, DbError> {
        let row = sqlx::query_as::<Sqlite, ModelRegistrationRow>(
            "SELECT * FROM model_registrations WHERE is_active = 1",
        )
        .fetch_optional(&self.pool)
        .await?;
        let Some(row) = row else {
            return Ok(None);
        };
        let kwargs = resolved_kwargs(&row.kwargs_json, &|k| std::env::var(k).ok());
        Ok(Some(ResolvedModel {
            model_key: row.key,
            label: row.label,
            class_path: row.class_path,
            kwargs,
            is_active: row.is_active,
        }))
    }

    /// Seed registrations from a `registry.json` file. No-op (returns 0) when the
    /// DB already has entries or the file is missing/unreadable (Python
    /// `seed_from_json`).
    ///
    /// # Errors
    /// Returns [`DbError`] on a query or registration failure.
    pub async fn seed_from_json(&self, json_path: &str) -> Result<usize, DbError> {
        let count: i64 =
            sqlx::query_scalar::<Sqlite, i64>("SELECT COUNT(*) FROM model_registrations")
                .fetch_one(&self.pool)
                .await?;
        if count > 0 {
            return Ok(0);
        }
        let Ok(text) = std::fs::read_to_string(json_path) else {
            return Ok(0);
        };
        let Ok(data) = serde_json::from_str::<Value>(&text) else {
            return Ok(0);
        };
        let active_key = data.get("active").and_then(Value::as_str).unwrap_or("");
        let models = data
            .get("models")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let mut imported = 0;
        for entry in &models {
            let key = entry.get("key").and_then(Value::as_str).unwrap_or("");
            if key.is_empty() {
                continue;
            }
            let factory = entry.get("factory").unwrap_or(entry);
            let class_path = factory
                .get("class_path")
                .and_then(Value::as_str)
                .unwrap_or("");
            let kwargs = factory
                .get("kwargs")
                .and_then(Value::as_object)
                .cloned()
                .unwrap_or_default();
            let label = entry.get("label").and_then(Value::as_str).unwrap_or(key);
            self.register(key, label, class_path, &kwargs, key == active_key)
                .await
                .map_err(|e| {
                    DbError::JsonDecode(serde_json::Error::io(std::io::Error::other(e.to_string())))
                })?;
            imported += 1;
        }
        Ok(imported)
    }
}

#[async_trait]
impl ModelStore for ModelRegistry {
    async fn register(
        &self,
        model_key: &str,
        label: &str,
        class_path: &str,
        kwargs: &JsonObject,
        activate: bool,
    ) -> Result<ModelRegistration, CoreError> {
        let now = OffsetDateTime::now_utc();
        let kwargs_json = json_col::encode(kwargs)?;
        let mut tx = self.pool.begin().await.map_err(DbError::from)?;
        if activate {
            sqlx::query("UPDATE model_registrations SET is_active = 0 WHERE is_active = 1")
                .execute(&mut *tx)
                .await
                .map_err(DbError::from)?;
        }
        let row = sqlx::query_as::<Sqlite, ModelRegistrationRow>(
            "INSERT INTO model_registrations (key, label, class_path, kwargs_json, is_active, created_at, updated_at) \
             VALUES (?, ?, ?, ?, ?, ?, ?) \
             ON CONFLICT(key) DO UPDATE SET \
               label = excluded.label, class_path = excluded.class_path, \
               kwargs_json = excluded.kwargs_json, updated_at = excluded.updated_at, \
               is_active = CASE WHEN excluded.is_active = 1 THEN 1 ELSE model_registrations.is_active END \
             RETURNING *",
        )
        .bind(model_key)
        .bind(label)
        .bind(class_path)
        .bind(kwargs_json)
        .bind(activate)
        .bind(now)
        .bind(now)
        .fetch_one(&mut *tx)
        .await
        .map_err(DbError::from)?;
        tx.commit().await.map_err(DbError::from)?;
        Ok(row_to_model(row, false))
    }

    async fn delete(&self, model_key: &str) -> Result<bool, CoreError> {
        let mut tx = self.pool.begin().await.map_err(DbError::from)?;
        let was_active: Option<bool> = sqlx::query_scalar::<Sqlite, bool>(
            "SELECT is_active FROM model_registrations WHERE key = ?",
        )
        .bind(model_key)
        .fetch_optional(&mut *tx)
        .await
        .map_err(DbError::from)?;
        let Some(was_active) = was_active else {
            return Ok(false);
        };
        sqlx::query("DELETE FROM model_registrations WHERE key = ?")
            .bind(model_key)
            .execute(&mut *tx)
            .await
            .map_err(DbError::from)?;
        if was_active {
            sqlx::query(
                "UPDATE model_registrations SET is_active = 1 \
                 WHERE id = (SELECT id FROM model_registrations ORDER BY created_at ASC, id ASC LIMIT 1)",
            )
            .execute(&mut *tx)
            .await
            .map_err(DbError::from)?;
        }
        tx.commit().await.map_err(DbError::from)?;
        Ok(true)
    }

    async fn get(&self, model_key: &str) -> Result<Option<ModelRegistration>, CoreError> {
        let row = sqlx::query_as::<Sqlite, ModelRegistrationRow>(
            "SELECT * FROM model_registrations WHERE key = ?",
        )
        .bind(model_key)
        .fetch_optional(&self.pool)
        .await
        .map_err(DbError::from)?;
        Ok(row.map(|r| row_to_model(r, true)))
    }

    async fn active(&self) -> Result<Option<ModelRegistration>, CoreError> {
        let row = sqlx::query_as::<Sqlite, ModelRegistrationRow>(
            "SELECT * FROM model_registrations WHERE is_active = 1",
        )
        .fetch_optional(&self.pool)
        .await
        .map_err(DbError::from)?;
        Ok(row.map(|r| row_to_model(r, true)))
    }
}

/// Map a row to the DTO, optionally redacting secret kwargs (`redact` mirrors the
/// Python `_to_dict(redact=...)` default: `True` for `get`/`active`, `False` for
/// `register`). The `key` column maps to the domain `model_key` (anchor §4).
fn row_to_model(row: ModelRegistrationRow, redact: bool) -> ModelRegistration {
    let kwargs_json = if redact {
        redact_kwargs_json(&row.kwargs_json)
    } else {
        row.kwargs_json
    };
    ModelRegistration {
        id: row.id,
        model_key: row.key,
        label: row.label,
        class_path: row.class_path,
        kwargs_json,
        is_active: row.is_active,
        created_at: UtcDateTime::from_offset(row.created_at),
        updated_at: UtcDateTime::from_offset(row.updated_at),
    }
}

fn redact_kwargs_json(kwargs_json: &str) -> String {
    match serde_json::from_str::<Value>(kwargs_json) {
        Ok(Value::Object(map)) => Value::Object(redact_secrets(&map)).to_string(),
        _ => "{}".to_owned(),
    }
}

fn redact_secrets(map: &Map<String, Value>) -> Map<String, Value> {
    let mut out = Map::new();
    for (key, value) in map {
        let lower = key.to_lowercase();
        if SECRET_MARKERS.iter().any(|m| lower.contains(m)) {
            match value {
                Value::String(s) if s.starts_with("env:") || s.starts_with('$') => {
                    out.insert(key.clone(), value.clone());
                }
                _ => {
                    out.insert(key.clone(), Value::String("***".to_owned()));
                }
            }
        } else if let Value::Object(inner) = value {
            out.insert(key.clone(), Value::Object(redact_secrets(inner)));
        } else {
            out.insert(key.clone(), value.clone());
        }
    }
    out
}

fn resolved_kwargs(kwargs_json: &str, lookup: &dyn Fn(&str) -> Option<String>) -> JsonObject {
    let parsed: Value =
        serde_json::from_str(kwargs_json).unwrap_or_else(|_| Value::Object(Map::new()));
    match resolve_placeholders(&parsed, lookup) {
        Value::Object(map) => map,
        _ => Map::new(),
    }
}

/// Resolve `env:VAR`, `${VAR}`, and `$VAR` placeholders recursively (Python
/// `_resolve_env_placeholders`). Unset variables resolve to `""`.
fn resolve_placeholders(value: &Value, lookup: &dyn Fn(&str) -> Option<String>) -> Value {
    match value {
        Value::String(s) => {
            if let Some(var) = s.strip_prefix("env:") {
                return Value::String(lookup(var).unwrap_or_default());
            }
            if let Some(var) = parse_dollar_var(s) {
                return Value::String(lookup(var).unwrap_or_default());
            }
            Value::String(s.clone())
        }
        Value::Object(map) => Value::Object(
            map.iter()
                .map(|(k, v)| (k.clone(), resolve_placeholders(v, lookup)))
                .collect(),
        ),
        Value::Array(items) => Value::Array(
            items
                .iter()
                .map(|v| resolve_placeholders(v, lookup))
                .collect(),
        ),
        other => other.clone(),
    }
}

/// Match a full `${VAR}` or `$VAR` placeholder (Python `re.fullmatch(r"\$\{(\w+)\}|\$(\w+)")`).
/// `is_word` uses Unicode `is_alphanumeric` (not ASCII-only) to match Python's
/// Unicode `\w` on a `str` (e.g. a `${VÄR}` placeholder resolves identically).
fn parse_dollar_var(s: &str) -> Option<&str> {
    let is_word = |v: &str| !v.is_empty() && v.chars().all(|c| c.is_alphanumeric() || c == '_');
    if let Some(inner) = s.strip_prefix("${").and_then(|r| r.strip_suffix('}')) {
        if is_word(inner) {
            return Some(inner);
        }
        return None;
    }
    if let Some(rest) = s.strip_prefix('$') {
        if is_word(rest) {
            return Some(rest);
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    async fn registry() -> (tempfile::TempDir, ModelRegistry) {
        let dir = tempfile::tempdir().expect("tempdir");
        let path = dir.path().join("models.db");
        let mut cfg = eos_config::DatabaseConfig::default();
        cfg.url =
            eos_config::DatabaseUrl::parse(format!("sqlite://{}", path.display())).expect("url");
        let pool = crate::pool::open_pool(&cfg).await.expect("pool");
        (dir, ModelRegistry::new(pool))
    }

    fn obj(pairs: &[(&str, Value)]) -> JsonObject {
        pairs
            .iter()
            .map(|(k, v)| ((*k).to_owned(), v.clone()))
            .collect()
    }

    #[test]
    fn dollar_var_parsing_is_fullmatch() {
        assert_eq!(parse_dollar_var("${FOO}"), Some("FOO"));
        assert_eq!(parse_dollar_var("$FOO_BAR"), Some("FOO_BAR"));
        assert_eq!(parse_dollar_var("prefix$FOO"), None);
        assert_eq!(parse_dollar_var("${}"), None);
        assert_eq!(parse_dollar_var("$"), None);
        assert_eq!(parse_dollar_var("plain"), None);
        // Unicode word chars match Python's `\w` (not ASCII-only).
        assert_eq!(parse_dollar_var("${VÄR}"), Some("VÄR"));
    }

    #[test]
    fn resolve_placeholders_uses_lookup() {
        let lookup = |k: &str| match k {
            "MY_KEY" => Some("secret-value".to_owned()),
            _ => None,
        };
        let input = json!({
            "api_key": "env:MY_KEY",
            "braced": "${MY_KEY}",
            "bare": "$MY_KEY",
            "missing": "env:NOPE",
            "literal": "keep-me",
            "nested": { "k": "$MY_KEY" },
        });
        let out = resolve_placeholders(&input, &lookup);
        assert_eq!(out["api_key"], json!("secret-value"));
        assert_eq!(out["braced"], json!("secret-value"));
        assert_eq!(out["bare"], json!("secret-value"));
        assert_eq!(out["missing"], json!("")); // unset -> ""
        assert_eq!(out["literal"], json!("keep-me"));
        assert_eq!(out["nested"]["k"], json!("secret-value"));
    }

    #[test]
    fn redaction_masks_secrets_keeps_placeholders() {
        let map = obj(&[
            ("api_key", json!("sk-realsecret")),
            ("auth_token", json!("env:TOKEN")), // placeholder kept visible
            ("model", json!("gpt-4")),          // non-secret kept
        ]);
        let redacted = redact_secrets(&map);
        assert_eq!(redacted["api_key"], json!("***"));
        assert_eq!(redacted["auth_token"], json!("env:TOKEN"));
        assert_eq!(redacted["model"], json!("gpt-4"));
    }

    // AC-eos-db-07: register/active/active_resolved, activation flips the active
    // row, delete promotes the oldest, class_path is returned verbatim.
    #[tokio::test]
    async fn model_registry_active_and_resolve() {
        let env_var = "EOS_DB_TEST_MODEL_KEY_AC07";
        // SAFETY: a uniquely-named var read only by this test.
        std::env::set_var(env_var, "resolved-secret");

        let (_dir, reg) = registry().await;

        let a = reg
            .register(
                "a",
                "Model A",
                "pkg.A",
                &obj(&[("model", json!("m-a"))]),
                true,
            )
            .await
            .expect("register a");
        assert_eq!(a.model_key, "a");
        assert_eq!(a.class_path, "pkg.A"); // class_path verbatim
        assert!(a.is_active);

        // Activating b deactivates a.
        let kwargs_b = obj(&[
            ("model", json!("m-b")),
            ("api_key", json!(format!("env:{env_var}"))),
        ]);
        reg.register("b", "Model B", "pkg.B", &kwargs_b, true)
            .await
            .expect("register b");
        let active = reg.active().await.expect("active").expect("some");
        assert_eq!(active.model_key, "b");
        // get/active redact the secret in kwargs_json (placeholder kept).
        assert!(active.kwargs_json.contains("env:"));

        // active_resolved resolves the env placeholder to the real value.
        let resolved = reg
            .active_resolved()
            .await
            .expect("resolved")
            .expect("some");
        assert_eq!(
            resolved.kwargs.get("api_key"),
            Some(&json!("resolved-secret"))
        );
        assert_eq!(resolved.class_path, "pkg.B");

        // Deleting the active row promotes the oldest remaining (a).
        assert!(reg.delete("b").await.expect("delete"));
        let active = reg.active().await.expect("active").expect("some");
        assert_eq!(active.model_key, "a");

        std::env::remove_var(env_var);
    }

    #[tokio::test]
    async fn seed_from_json_imports_once() {
        let (dir, reg) = registry().await;
        let seed = dir.path().join("registry.json");
        std::fs::write(
            &seed,
            serde_json::to_string(&json!({
                "active": "k2",
                "models": [
                    { "key": "k1", "label": "One", "factory": { "class_path": "p.One", "kwargs": {} } },
                    { "key": "k2", "label": "Two", "factory": { "class_path": "p.Two", "kwargs": { "model": "x" } } }
                ]
            }))
            .expect("json"),
        )
        .expect("write seed");

        let n = reg
            .seed_from_json(seed.to_str().expect("path"))
            .await
            .expect("seed");
        assert_eq!(n, 2);
        assert_eq!(
            reg.active().await.expect("active").expect("some").model_key,
            "k2"
        );
        // Second seed is a no-op (DB already populated).
        assert_eq!(
            reg.seed_from_json(seed.to_str().expect("path"))
                .await
                .expect("seed2"),
            0
        );
    }
}
