//! `sandbox-console` binary: parse flags, discover config, and run the
//! console HTTP server.

use std::path::PathBuf;
use std::process::ExitCode;

use clap::Parser;

use sandbox_cli_core::GatewayConfigOverrides;
use sandbox_console::config::{ConsoleConfig, ConsoleConfigOverrides};
use sandbox_console::server;
use sandbox_console::state::AppState;

#[derive(Debug, Parser)]
#[command(name = "sandbox-console")]
struct Cli {
    #[arg(long = "bind", value_name = "HOST:PORT")]
    bind: Option<String>,

    #[arg(long = "gateway-socket", value_name = "HOST:PORT")]
    gateway_socket_path: Option<PathBuf>,

    #[arg(long = "gateway-auth-token", value_name = "TOKEN")]
    gateway_auth_token: Option<String>,

    #[arg(long = "assets", value_name = "DIR")]
    assets_dir: Option<PathBuf>,
}

#[tokio::main]
async fn main() -> ExitCode {
    let cli = Cli::parse();
    let overrides = ConsoleConfigOverrides {
        bind: cli.bind,
        assets_dir: cli.assets_dir,
        gateway: GatewayConfigOverrides {
            gateway_socket_path: cli.gateway_socket_path,
            gateway_auth_token: cli.gateway_auth_token,
        },
    };
    let config = match ConsoleConfig::discover(overrides) {
        Ok(config) => config,
        Err(error) => {
            eprintln!("sandbox-console config error: {error}");
            return ExitCode::from(2);
        }
    };
    match server::run(AppState::new(config)).await {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("sandbox-console server error: {error}");
            ExitCode::FAILURE
        }
    }
}
