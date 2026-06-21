use std::process::ExitCode;

#[tokio::main]
async fn main() -> ExitCode {
    sandbox_gateway::cli::output::run_cli(std::env::args_os()).await
}
