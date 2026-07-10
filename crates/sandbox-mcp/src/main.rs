use std::process::ExitCode;

use clap::Parser;
use sandbox_mcp::config::Cli;

#[tokio::main]
async fn main() -> ExitCode {
    let cli = Cli::parse();
    let gateway = match cli.discover_gateway() {
        Ok(gateway) => gateway,
        Err(error) => {
            eprintln!("sandbox-mcp configuration error: {error}");
            return ExitCode::from(2);
        }
    };
    match sandbox_mcp::run(cli.set, gateway).await {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("sandbox-mcp server error: {error}");
            ExitCode::FAILURE
        }
    }
}
