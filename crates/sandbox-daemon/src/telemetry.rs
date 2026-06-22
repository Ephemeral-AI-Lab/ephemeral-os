//! Daemon-owned tracing subscriber setup.

use sandbox_config::configs::validate::{require_non_empty, ConfigFieldError};
use sandbox_config::{ConfigDocument, ConfigError};
use serde::Deserialize;
use thiserror::Error;
use tracing_subscriber::filter::LevelFilter;
use tracing_subscriber::fmt::format::FmtSpan;
use tracing_subscriber::fmt::MakeWriter;
use tracing_subscriber::util::SubscriberInitExt;

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TelemetryConfig {
    pub enabled: bool,
    pub service_name: String,
    pub level: String,
    #[serde(default)]
    pub sink: Option<TelemetrySink>,
}

impl Default for TelemetryConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            service_name: "sandbox-daemon".to_owned(),
            level: "info".to_owned(),
            sink: None,
        }
    }
}

impl TelemetryConfig {
    /// Validate semantic constraints that YAML deserialization cannot express.
    ///
    /// # Errors
    /// Returns an error when the telemetry config is internally inconsistent.
    pub fn validate(&self) -> Result<(), ConfigFieldError> {
        require_non_empty(&self.service_name, "daemon.telemetry.service_name")?;
        validate_telemetry_level(&self.level)?;
        if self.enabled && self.sink.is_none() {
            return Err(ConfigFieldError::new(
                "daemon.telemetry.sink",
                "enabled telemetry requires exactly one sink",
            ));
        }
        Ok(())
    }

    /// Validate serve-mode constraints for daemon-owned telemetry sinks.
    ///
    /// # Errors
    /// Returns an error when the configured sink cannot run in `mode`.
    pub fn validate_for_serve_mode(&self, mode: DaemonServeMode) -> Result<(), ConfigFieldError> {
        self.validate()?;
        if !self.enabled {
            return Ok(());
        }
        if matches!(
            (mode, &self.sink),
            (
                DaemonServeMode::Spawn,
                Some(TelemetrySink::LocalJson { .. })
            )
        ) {
            return Err(ConfigFieldError::new(
                "daemon.telemetry.sink",
                "local_json stdout/stderr telemetry requires foreground serve mode",
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum TelemetrySink {
    LocalJson { stream: TelemetryOutputStream },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TelemetryOutputStream {
    Stdout,
    Stderr,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DaemonServeMode {
    Foreground,
    Spawn,
}

/// Deserialize daemon telemetry from the shared config document.
///
/// # Errors
/// Returns an error when the `daemon` section or nested `telemetry` section
/// cannot be deserialized.
pub fn from_config_document(doc: &ConfigDocument) -> Result<TelemetryConfig, ConfigError> {
    doc.section::<DaemonTelemetrySection>("daemon")
        .map(|section| section.telemetry)
}

/// Install the process-global telemetry subscriber when daemon telemetry is on.
///
/// # Errors
/// Returns an error when the configured level is invalid or a subscriber has
/// already been installed.
pub fn install(config: &TelemetryConfig) -> Result<(), TelemetryInstallError> {
    if !config.enabled {
        return Ok(());
    }
    let level = level_filter(&config.level)?;
    match config.sink.as_ref() {
        Some(TelemetrySink::LocalJson {
            stream: TelemetryOutputStream::Stdout,
        }) => init_json_subscriber(level, std::io::stdout),
        Some(TelemetrySink::LocalJson {
            stream: TelemetryOutputStream::Stderr,
        }) => init_json_subscriber(level, std::io::stderr),
        None => Err(TelemetryInstallError::MissingSink),
    }
}

fn init_json_subscriber<W>(level: LevelFilter, writer: W) -> Result<(), TelemetryInstallError>
where
    W: for<'writer> MakeWriter<'writer> + Send + Sync + 'static,
{
    json_subscriber(level, writer)
        .try_init()
        .map_err(|_| TelemetryInstallError::SubscriberAlreadyInstalled)
}

fn json_subscriber<W>(
    level: LevelFilter,
    writer: W,
) -> impl tracing::Subscriber + Send + Sync + 'static
where
    W: for<'writer> MakeWriter<'writer> + Send + Sync + 'static,
{
    tracing_subscriber::fmt()
        .json()
        .with_writer(writer)
        .with_max_level(level)
        .with_current_span(true)
        .with_span_list(true)
        .with_span_events(FmtSpan::CLOSE)
        .finish()
}

fn level_filter(level: &str) -> Result<LevelFilter, TelemetryInstallError> {
    match level {
        "trace" => Ok(LevelFilter::TRACE),
        "debug" => Ok(LevelFilter::DEBUG),
        "info" => Ok(LevelFilter::INFO),
        "warn" => Ok(LevelFilter::WARN),
        "error" => Ok(LevelFilter::ERROR),
        _ => Err(TelemetryInstallError::InvalidLevel),
    }
}

fn validate_telemetry_level(level: &str) -> Result<(), ConfigFieldError> {
    match level {
        "trace" | "debug" | "info" | "warn" | "error" => Ok(()),
        _ => Err(ConfigFieldError::new(
            "daemon.telemetry.level",
            "must be one of trace, debug, info, warn, error",
        )),
    }
}

#[derive(Debug, Error)]
pub enum TelemetryInstallError {
    #[error("telemetry level is invalid")]
    InvalidLevel,
    #[error("enabled telemetry requires a sink")]
    MissingSink,
    #[error("tracing subscriber is already installed")]
    SubscriberAlreadyInstalled,
}

#[cfg(test)]
#[allow(dead_code, reason = "used by path-included daemon integration tests")]
pub(crate) fn with_test_json_subscriber<W, T>(
    config: &TelemetryConfig,
    writer: W,
    run: impl FnOnce() -> T,
) -> Result<T, TelemetryInstallError>
where
    W: for<'writer> MakeWriter<'writer> + Send + Sync + 'static,
{
    if !config.enabled {
        return Ok(run());
    }
    let level = level_filter(&config.level)?;
    Ok(tracing::subscriber::with_default(
        json_subscriber(level, writer),
        run,
    ))
}

#[derive(Deserialize)]
struct DaemonTelemetrySection {
    #[serde(default)]
    telemetry: TelemetryConfig,
}
