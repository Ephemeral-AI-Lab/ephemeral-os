//! eos-runtime binary entry point.
//!
//! Thin by design (`proj-lib-main-split`): it initializes tracing, constructs the
//! single multi-thread Tokio runtime, builds the [`AppState`] graph, and — when a
//! prompt argument is given — starts one request and awaits it. All logic lives
//! in the library.
#![forbid(unsafe_code)]

use eos_runtime::observability::{init_tracing, LogFormat};
use eos_runtime::{start_request, AppState};

fn main() -> anyhow::Result<()> {
    init_tracing(LogFormat::Text).map_err(|err| anyhow::anyhow!(err.to_string()))?;

    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()?;

    runtime.block_on(async {
        let state = build_app_state().await?;
        tracing::info!("eos-runtime app state constructed");

        if let Some(prompt) = std::env::args().nth(1) {
            let handle = start_request(&state, prompt, None, None).await?;
            tracing::info!(request_id = %handle.request_id, "request started");
            handle.join().await;
        }
        state.flush_audit();
        anyhow::Ok(())
    })
}

/// Repo-relative agent-profile tree used as the default registry source for the
/// shipped binary. The canonical bundle lives at `.eos-agents/` (profiles under
/// `profile/`, their coupled skills under `skills/`), relocated off the retiring
/// Python backend.
const DEFAULT_AGENTS_DIR: &str = ".eos-agents/profile";

/// Build the application state, seeding the agent registry so `root` resolves
/// (`request_completion` NF1 — the binary otherwise ships with an empty registry
/// and fails every request at root resolution).
///
/// `EOS_AGENTS_DIR` overrides the source and is validated normally. Otherwise we
/// fall back to the repo-relative bundled tree when present (it only resolves
/// when run from the repo root; a missing dir yields an empty registry, the
/// prior no-op behavior). The bundled profiles name `lsp.*` tools not yet ported
/// to the Rust tool registry, so `compatibility_mode` masks *that* known gap for
/// this pre-cutover demo binary — it disables agent-tool validation wholesale, so
/// it is scoped to the bundled path only, never an explicit override.
async fn build_app_state() -> anyhow::Result<AppState> {
    let mut builder = AppState::builder();
    if let Ok(dir) = std::env::var("EOS_AGENTS_DIR") {
        builder = builder.agents_dir(dir);
    } else if std::path::Path::new(DEFAULT_AGENTS_DIR).is_dir() {
        builder = builder
            .agents_dir(DEFAULT_AGENTS_DIR)
            .compatibility_mode(true);
    }
    builder.build().await
}
