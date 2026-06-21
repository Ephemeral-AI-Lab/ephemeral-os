use std::ffi::OsString;
use std::io::{self, Write};
use std::path::PathBuf;
use std::process::ExitCode;

use clap::error::ErrorKind;
use clap::{Args, Parser, Subcommand};
use serde_json::{json, Value};

use crate::client::GatewayClient;
use crate::config::{GatewayConfig, GatewayConfigOverrides};
use crate::request_builder::{
    build_request_from_catalog, catalog_from_response, manager_catalog_request,
    resolve_runtime_sandbox_id, runtime_catalog_request, BuildRequestInput, RequestBuildError,
};
use sandbox_protocol::{
    manual::render_catalog_manual, OperationCatalogDocument, OperationExecutionSpace,
};

const EXIT_SUCCESS: u8 = 0;
const EXIT_FAILURE: u8 = 1;
const EXIT_USAGE: u8 = 2;

#[derive(Debug, Parser)]
#[command(name = "sandbox", disable_help_subcommand = true)]
struct Cli {
    #[arg(long = "gateway-socket", value_name = "PATH", global = true)]
    gateway_socket_path: Option<PathBuf>,

    #[arg(
        long = "manager-socket",
        value_name = "PATH",
        global = true,
        help = "Deprecated alias for --gateway-socket."
    )]
    manager_socket_path: Option<PathBuf>,

    #[arg(long = "default-sandbox-id", value_name = "SANDBOX_ID", global = true)]
    default_sandbox_id: Option<String>,

    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    Manager(OperationCommand),
    Runtime(RuntimeCommand),
    Manual(ManualCommand),
}

#[derive(Debug, Args)]
struct OperationCommand {
    operation: String,

    #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
    operation_argv: Vec<String>,
}

#[derive(Debug, Args)]
struct RuntimeCommand {
    #[arg(long = "sandbox-id", value_name = "SANDBOX_ID")]
    sandbox_id: Option<String>,

    operation: String,

    #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
    operation_argv: Vec<String>,
}

#[derive(Debug, Args)]
struct ManualCommand {
    #[arg(long = "sandbox-id", value_name = "SANDBOX_ID")]
    sandbox_id: Option<String>,
}

pub async fn run_cli<I, T>(args: I) -> ExitCode
where
    I: IntoIterator<Item = T>,
    T: Into<OsString> + Clone,
{
    let mut stdout = io::stdout().lock();
    let mut stderr = io::stderr().lock();
    ExitCode::from(run_cli_with_writers(args, &mut stdout, &mut stderr).await)
}

pub async fn run_cli_with_writers<I, T, WOut, WErr>(
    args: I,
    stdout: &mut WOut,
    stderr: &mut WErr,
) -> u8
where
    I: IntoIterator<Item = T>,
    T: Into<OsString> + Clone,
    WOut: Write,
    WErr: Write,
{
    let cli = match Cli::try_parse_from(args) {
        Ok(cli) => cli,
        Err(error) => {
            if matches!(
                error.kind(),
                ErrorKind::DisplayHelp | ErrorKind::DisplayVersion
            ) {
                let _ = write!(stdout, "{error}");
                return EXIT_SUCCESS;
            }
            let _ = write!(stderr, "{error}");
            return EXIT_USAGE;
        }
    };

    let config = match GatewayConfig::discover(GatewayConfigOverrides {
        gateway_socket_path: cli.gateway_socket_path,
        manager_socket_path: cli.manager_socket_path,
        default_sandbox_id: cli.default_sandbox_id,
    }) {
        Ok(config) => config,
        Err(error) => {
            let _ = render_error("config_error", error.to_string(), stderr);
            return EXIT_USAGE;
        }
    };

    let client = GatewayClient::new(config.gateway_socket_path.clone());

    let request_input = match cli.command {
        Command::Manager(command) => BuildRequestInput {
            execution_space: OperationExecutionSpace::Manager,
            operation: command.operation,
            operation_argv: command.operation_argv,
            sandbox_id: None,
        },
        Command::Runtime(command) => {
            let sandbox_id = match resolve_runtime_sandbox_id(command.sandbox_id, &config) {
                Ok(sandbox_id) => sandbox_id,
                Err(error) => {
                    let _ = render_request_error(&error, stderr);
                    return EXIT_USAGE;
                }
            };
            let catalog_request = runtime_catalog_request(sandbox_id.clone());
            let catalog = match load_catalog(&client, &catalog_request, stderr).await {
                Ok(catalog) => catalog,
                Err(exit_code) => return exit_code,
            };
            let request_input = BuildRequestInput {
                execution_space: OperationExecutionSpace::Runtime,
                operation: command.operation,
                operation_argv: command.operation_argv,
                sandbox_id: Some(sandbox_id),
            };
            return run_request_from_catalog(
                &client,
                request_input,
                &config,
                &catalog,
                stdout,
                stderr,
            )
            .await;
        }
        Command::Manual(command) => {
            return run_manual_command(&client, command, &config, stdout, stderr).await;
        }
    };

    let catalog_request = manager_catalog_request();
    let catalog = match load_catalog(&client, &catalog_request, stderr).await {
        Ok(catalog) => catalog,
        Err(exit_code) => return exit_code,
    };

    run_request_from_catalog(&client, request_input, &config, &catalog, stdout, stderr).await
}

async fn run_request_from_catalog<WOut, WErr>(
    client: &GatewayClient,
    request_input: BuildRequestInput,
    config: &GatewayConfig,
    catalog: &OperationCatalogDocument,
    stdout: &mut WOut,
    stderr: &mut WErr,
) -> u8
where
    WOut: Write,
    WErr: Write,
{
    let request = match build_request_from_catalog(request_input, config, catalog) {
        Ok(request) => request,
        Err(error) => {
            let _ = render_request_error(&error, stderr);
            return EXIT_USAGE;
        }
    };

    let response = match client.send(&request).await {
        Ok(response) => response,
        Err(error) => {
            let _ = render_error(error.kind(), error.to_string(), stderr);
            return EXIT_FAILURE;
        }
    };

    render_response(&response, stdout, stderr).unwrap_or(EXIT_FAILURE)
}

async fn run_manual_command<WOut, WErr>(
    client: &GatewayClient,
    command: ManualCommand,
    config: &GatewayConfig,
    stdout: &mut WOut,
    stderr: &mut WErr,
) -> u8
where
    WOut: Write,
    WErr: Write,
{
    let manager_catalog = match load_catalog(client, &manager_catalog_request(), stderr).await {
        Ok(catalog) => catalog,
        Err(exit_code) => return exit_code,
    };
    let runtime_sandbox_id = command
        .sandbox_id
        .or_else(|| config.default_sandbox_id.clone());
    let runtime_catalog = match runtime_sandbox_id {
        Some(sandbox_id) => {
            match load_catalog(client, &runtime_catalog_request(sandbox_id), stderr).await {
                Ok(catalog) => Some(catalog),
                Err(exit_code) => return exit_code,
            }
        }
        None => None,
    };
    write_manual(
        &render_catalog_manual(&manager_catalog, runtime_catalog.as_ref()),
        stdout,
        stderr,
    )
}

async fn load_catalog<WErr>(
    client: &GatewayClient,
    request: &sandbox_protocol::Request,
    stderr: &mut WErr,
) -> Result<OperationCatalogDocument, u8>
where
    WErr: Write,
{
    let response = match client.send(request).await {
        Ok(response) => response,
        Err(error) => {
            let _ = render_error(error.kind(), error.to_string(), stderr);
            return Err(EXIT_FAILURE);
        }
    };
    match catalog_from_response(&response) {
        Ok(catalog) => Ok(catalog),
        Err(error) => {
            let _ = render_error("protocol_error", error.to_string(), stderr);
            Err(EXIT_FAILURE)
        }
    }
}

pub fn render_response<WOut, WErr>(
    response: &Value,
    stdout: &mut WOut,
    stderr: &mut WErr,
) -> io::Result<u8>
where
    WOut: Write,
    WErr: Write,
{
    if response.get("error").is_some() {
        write_json_line(stderr, response)?;
        Ok(EXIT_FAILURE)
    } else {
        write_json_line(stdout, response)?;
        Ok(EXIT_SUCCESS)
    }
}

fn render_error<WErr>(
    kind: &'static str,
    message: impl Into<String>,
    stderr: &mut WErr,
) -> io::Result<()>
where
    WErr: Write,
{
    let response = sandbox_protocol::error_response_with_details(kind, message, json!({}));
    write_json_line(stderr, &response)
}

fn render_request_error<WErr>(error: &RequestBuildError, stderr: &mut WErr) -> io::Result<()>
where
    WErr: Write,
{
    render_error("invalid_request", error.message(), stderr)
}

fn write_manual<WOut, WErr>(manual: &str, stdout: &mut WOut, stderr: &mut WErr) -> u8
where
    WOut: Write,
    WErr: Write,
{
    match stdout.write_all(manual.as_bytes()) {
        Ok(()) => EXIT_SUCCESS,
        Err(error) => {
            let _ = render_error("output_error", error.to_string(), stderr);
            EXIT_FAILURE
        }
    }
}

fn write_json_line<W>(writer: &mut W, value: &Value) -> io::Result<()>
where
    W: Write,
{
    writer.write_all(&json_line(value))
}

fn json_line(value: &Value) -> Vec<u8> {
    let mut line = serde_json::to_vec(value).unwrap_or_default();
    line.push(b'\n');
    line
}
