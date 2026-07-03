use std::process::ExitCode;

#[tokio::main]
async fn main() -> ExitCode {
    sandbox_manager_cli::run_cli(std::env::args_os()).await
}
