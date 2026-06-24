use std::path::PathBuf;
use std::sync::OnceLock;

use crate::assertion;
use crate::cli_client::{CallRecord, CliClient};
use crate::config::RunConfig;
use crate::gateway;

const CLI_BIN: &str = "sandbox-cli";

/// Lazy harness singleton: env → manifest → `CliClient`, plus the per-test
/// provisioning entry point. Owns the one `CliClient` every leaf shares.
pub struct Harness {
    cli: CliClient,
    run_root: PathBuf,
    run_id: String,
    image: String,
}

impl Harness {
    /// Lazy singleton. Reads `EOS_E2E_RUN_ROOT` → `run-manifest.json` once.
    /// Returns `None` when `EOS_E2E_RUN_ROOT` is unset (skip signal for every
    /// leaf); panics only when the env is set but the manifest is
    /// missing/invalid (a real misconfiguration), never on the unset path.
    pub fn get() -> Option<&'static Harness> {
        static HARNESS: OnceLock<Option<Harness>> = OnceLock::new();
        HARNESS.get_or_init(Harness::init).as_ref()
    }

    fn init() -> Option<Harness> {
        let config = match RunConfig::from_env() {
            Ok(config) => config?,
            Err(error) => panic!("invalid EOS_E2E_RUN_ROOT run-manifest.json: {error:#}"),
        };
        if let Err(error) = gateway::await_ready(&config.gateway_socket) {
            panic!("gateway not ready: {error:#}");
        }
        let cli = CliClient::new(PathBuf::from(CLI_BIN), config.gateway_socket);
        Some(Harness {
            cli,
            run_root: config.run_root,
            run_id: config.run_id,
            image: config.image,
        })
    }

    #[must_use]
    pub fn cli(&self) -> &CliClient {
        &self.cli
    }

    /// Provision via the public manager CLI — the same path as the system under
    /// test. Creates `{run_root}/work/{run_id}-{slug}` as an absolute dir, then
    /// `sandbox-cli manager create_sandbox --image {image} --workspace-root {ws}`.
    /// The id is read from the create response `/id` (runtime-assigned,
    /// round-tripped), never predicted. Returns the RAII `Sandbox` and the create
    /// `CallRecord` so a leaf asserts on the one creation it made — no second
    /// `create_sandbox` is ever issued.
    pub fn provision_sandbox(&self, slug: &str, image: Option<&str>) -> (Sandbox, CallRecord) {
        let image = image.unwrap_or(&self.image);
        let workspace_root = self
            .run_root
            .join("work")
            .join(format!("{}-{slug}", self.run_id));
        std::fs::create_dir_all(&workspace_root).unwrap_or_else(|error| {
            panic!(
                "failed to create workspace root {}: {error}",
                workspace_root.display()
            )
        });
        let workspace_root = workspace_root
            .canonicalize()
            .unwrap_or_else(|error| panic!("failed to canonicalize workspace root: {error}"));
        let workspace_root_arg = workspace_root.to_string_lossy().into_owned();

        let record = self.cli.manager(
            "create_sandbox",
            &["--image", image, "--workspace-root", &workspace_root_arg],
        );
        let resp = record.response();
        assertion::ok(resp);
        let id = assertion::field(resp, "/id")
            .as_str()
            .expect("create_sandbox response /id is a string")
            .to_owned();

        (Sandbox { id, workspace_root }, record)
    }
}

/// RAII sandbox handle. On drop, issues
/// `sandbox-cli manager destroy_sandbox --sandbox-id {id}` (idempotent), making
/// teardown panic-safe even when an assertion fails.
pub struct Sandbox {
    pub id: String,
    pub workspace_root: PathBuf,
}

impl Drop for Sandbox {
    fn drop(&mut self) {
        if let Some(harness) = Harness::get() {
            let _ = harness
                .cli()
                .manager("destroy_sandbox", &["--sandbox-id", &self.id]);
        }
    }
}
