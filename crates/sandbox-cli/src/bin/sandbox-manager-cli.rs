use std::process::ExitCode;

#[tokio::main]
async fn main() -> ExitCode {
    sandbox_cli::manager::run_cli(std::env::args_os()).await
}
