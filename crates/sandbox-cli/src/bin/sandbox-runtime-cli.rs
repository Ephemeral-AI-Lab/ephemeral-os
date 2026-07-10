use std::process::ExitCode;

#[tokio::main]
async fn main() -> ExitCode {
    sandbox_cli::runtime::run_cli(std::env::args_os()).await
}
