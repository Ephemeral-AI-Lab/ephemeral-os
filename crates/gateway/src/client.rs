use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use anyhow::{bail, Context, Result};
use serde_json::{json, Value};

use crate::serve;
use crate::transport;

const DEFAULT_LAYER_STACK_ROOT: &str = "/eos/layer-stack";

pub(crate) fn run_host(argv: impl Iterator<Item = String>) -> Result<()> {
    let mut args = argv.collect::<Vec<_>>();
    let options = ClientOptions::parse(&mut args)?;
    let request = request_from_host(args, &options)?;
    send_and_print(request, &options)
}

pub(crate) fn run_daemon(argv: impl Iterator<Item = String>) -> Result<()> {
    let mut args = argv.collect::<Vec<_>>();
    let options = ClientOptions::parse(&mut args)?;
    let request = request_from_daemon(args, &options)?;
    send_and_print(request, &options)
}

pub(crate) fn print_usage() {
    println!(
        "\
usage:
  sandbox-gateway host serve [--listen PATH] [--image IMAGE] [--platform PLATFORM]

  sandbox-gateway host images profiles
  sandbox-gateway host images list
  sandbox-gateway host images pull IMAGE [--platform PLATFORM]
  sandbox-gateway host containers list
  sandbox-gateway host containers start IMAGE [--name NAME] [--platform PLATFORM]
  sandbox-gateway host containers adopt CONTAINER [--sandbox-id SANDBOX_ID] [--tcp-port PORT] [--auth-token TOKEN]
  sandbox-gateway host containers stop CONTAINER
  sandbox-gateway host containers stop --sandbox-id SANDBOX_ID
  sandbox-gateway host containers remove CONTAINER
  sandbox-gateway host containers remove --sandbox-id SANDBOX_ID
  sandbox-gateway host sandboxes acquire [--image-profile NAME] [--workspace-root PATH]
  sandbox-gateway host sandboxes list
  sandbox-gateway host sandboxes status SANDBOX_ID
  sandbox-gateway host sandboxes release SANDBOX_ID
  sandbox-gateway host traces list [--sandbox-id SANDBOX_ID] [--limit N]
  sandbox-gateway host traces show TRACE_ID [--limit N]
  sandbox-gateway host traces verify [TRACE_ID]
  sandbox-gateway host op OP [ARGS_JSON] [--sandbox-id SANDBOX_ID] [--operator]

  sandbox-gateway daemon --sandbox-id SANDBOX_ID ping
  sandbox-gateway daemon --sandbox-id SANDBOX_ID commands exec -- COMMAND
  sandbox-gateway daemon --sandbox-id SANDBOX_ID commands stdin COMMAND_ID TEXT
  sandbox-gateway daemon --sandbox-id SANDBOX_ID commands poll COMMAND_ID [--last-n-lines N]
  sandbox-gateway daemon --sandbox-id SANDBOX_ID commands cancel COMMAND_ID
  sandbox-gateway daemon --sandbox-id SANDBOX_ID commands collect [COMMAND_ID...]
  sandbox-gateway daemon --sandbox-id SANDBOX_ID commands count [--caller-id ID]
  sandbox-gateway daemon --sandbox-id SANDBOX_ID run end --caller-id ID [--grace-s SECONDS]
  sandbox-gateway daemon --sandbox-id SANDBOX_ID run cancel-all [--grace-s SECONDS]
  sandbox-gateway daemon --sandbox-id SANDBOX_ID op OP [ARGS_JSON] [--operator]

common client flags:
  --socket PATH      gateway client socket; overrides EOS_GATEWAY_SOCKET/default
  --operator         connect to <socket>.operator
  --envelope         print the full response envelope instead of result only"
    );
}

#[derive(Debug)]
pub(crate) struct ClientOptions {
    pub(crate) socket: PathBuf,
    pub(crate) operator: bool,
    pub(crate) envelope: bool,
    pub(crate) sandbox_id: Option<String>,
}

impl ClientOptions {
    fn parse(args: &mut Vec<String>) -> Result<Self> {
        let mut socket = serve::default_listen_path();
        let mut operator = false;
        let mut envelope = false;
        let mut sandbox_id = None;
        let mut index = 0;
        while index < args.len() {
            match args[index].as_str() {
                "--socket" => {
                    let value = take_flag_value(args, index, "--socket")?;
                    socket = value.into();
                }
                "--operator" => {
                    args.remove(index);
                    operator = true;
                }
                "--envelope" => {
                    args.remove(index);
                    envelope = true;
                }
                "--sandbox-id" => {
                    sandbox_id = Some(take_flag_value(args, index, "--sandbox-id")?);
                }
                _ => index += 1,
            }
        }
        Ok(Self {
            socket,
            operator,
            envelope,
            sandbox_id,
        })
    }
}

#[derive(Debug)]
pub(crate) struct GatewayRequest {
    pub(crate) op: String,
    pub(crate) args: Value,
    pub(crate) sandbox_id: Option<String>,
    pub(crate) operator: bool,
}

pub(crate) fn request_from_host(
    mut args: Vec<String>,
    options: &ClientOptions,
) -> Result<GatewayRequest> {
    let Some(group) = shift(&mut args) else {
        bail!("missing host command; expected images | containers | sandboxes | traces | op")
    };
    match group.as_str() {
        "images" => request_from_host_images(args),
        "containers" => request_from_host_containers(args, options),
        "sandboxes" => request_from_host_sandboxes(args, options),
        "traces" => request_from_host_traces(args, options),
        "op" => request_from_op(args, options.sandbox_id.clone(), false),
        other => bail!(
            "unknown host command {other:?}; expected images | containers | sandboxes | traces | op"
        ),
    }
}

pub(crate) fn request_from_daemon(
    mut args: Vec<String>,
    options: &ClientOptions,
) -> Result<GatewayRequest> {
    let Some(group) = shift(&mut args) else {
        bail!("missing daemon command; expected ping | commands | run | op")
    };
    let sandbox_id = require_sandbox_id(options)?;
    match group.as_str() {
        "ping" => {
            expect_no_args(&args)?;
            Ok(daemon_request(
                "sandbox.call.heartbeat",
                json!({}),
                sandbox_id,
                false,
            ))
        }
        "commands" => request_from_daemon_commands(args, sandbox_id),
        "run" => request_from_daemon_run(args, sandbox_id),
        "op" => request_from_op(args, Some(sandbox_id), false),
        other => bail!("unknown daemon command {other:?}; expected ping | commands | run | op"),
    }
}

fn request_from_host_images(mut args: Vec<String>) -> Result<GatewayRequest> {
    let Some(subcommand) = shift(&mut args) else {
        bail!("missing host images subcommand; expected profiles | list | pull")
    };
    match subcommand.as_str() {
        "profiles" => {
            expect_no_args(&args)?;
            Ok(host_request("host.image_profiles.list", json!({}), false))
        }
        "list" => {
            expect_no_args(&args)?;
            Ok(host_request("host.image.list", json!({}), true))
        }
        "pull" => {
            let platform = take_optional_flag(&mut args, "--platform")?;
            let Some(image) = shift(&mut args) else {
                bail!("host images pull requires IMAGE")
            };
            expect_no_args(&args)?;
            let mut body = json!({ "image": image });
            insert_optional(&mut body, "platform", platform);
            Ok(host_request("host.image.pull", body, true))
        }
        other => bail!("unknown host images subcommand {other:?}; expected profiles | list | pull"),
    }
}

fn request_from_host_containers(
    mut args: Vec<String>,
    options: &ClientOptions,
) -> Result<GatewayRequest> {
    let Some(subcommand) = shift(&mut args) else {
        bail!("missing host containers subcommand; expected list | start | adopt | stop | remove")
    };
    match subcommand.as_str() {
        "list" => {
            expect_no_args(&args)?;
            Ok(host_request("host.container.list", json!({}), true))
        }
        "start" => {
            let name = take_optional_flag(&mut args, "--name")?;
            let platform = take_optional_flag(&mut args, "--platform")?;
            let Some(image) = shift(&mut args) else {
                bail!("host containers start requires IMAGE")
            };
            expect_no_args(&args)?;
            let mut body = json!({ "image": image });
            insert_optional(&mut body, "name", name);
            insert_optional(&mut body, "platform", platform);
            Ok(host_request("host.container.start", body, true))
        }
        "adopt" => {
            let tcp_port = take_optional_u64(&mut args, "--tcp-port")?;
            let auth_token = take_optional_flag(&mut args, "--auth-token")?;
            let forward_auth_token = take_optional_flag(&mut args, "--forward-auth-token")?;
            let Some(container) = shift(&mut args) else {
                bail!("host containers adopt requires CONTAINER")
            };
            expect_no_args(&args)?;
            let mut body = json!({ "container": container });
            insert_optional(&mut body, "sandbox_id", options.sandbox_id.clone());
            insert_optional(&mut body, "auth_token", auth_token);
            insert_optional(&mut body, "forward_auth_token", forward_auth_token);
            if let Some(port) = tcp_port {
                body["tcp_port"] = json!(port);
            }
            Ok(host_request("host.container.adopt", body, true))
        }
        "stop" => request_from_container_target("host.container.stop", args, options),
        "remove" => request_from_container_target("host.container.remove", args, options),
        other => bail!(
            "unknown host containers subcommand {other:?}; expected list | start | adopt | stop | remove"
        ),
    }
}

fn request_from_container_target(
    op: &str,
    mut args: Vec<String>,
    options: &ClientOptions,
) -> Result<GatewayRequest> {
    if let Some(sandbox_id) = &options.sandbox_id {
        expect_no_args(&args)?;
        return Ok(host_request(op, json!({ "sandbox_id": sandbox_id }), true));
    }
    let Some(container) = shift(&mut args) else {
        bail!("{op} requires CONTAINER or --sandbox-id SANDBOX_ID")
    };
    expect_no_args(&args)?;
    Ok(host_request(op, json!({ "container": container }), true))
}

fn request_from_host_sandboxes(
    mut args: Vec<String>,
    _options: &ClientOptions,
) -> Result<GatewayRequest> {
    let Some(subcommand) = shift(&mut args) else {
        bail!("missing host sandboxes subcommand; expected acquire | list | status | release")
    };
    match subcommand.as_str() {
        "acquire" => {
            let image_profile = take_optional_flag(&mut args, "--image-profile")?;
            let workspace_root =
                take_optional_flag_any(&mut args, &["--workspace-root", "--workspace_root"])?;
            expect_no_args(&args)?;
            let mut body = json!({});
            insert_optional(&mut body, "image_profile", image_profile);
            insert_optional(&mut body, "workspace_root", workspace_root);
            Ok(host_request("host.sandbox.acquire", body, false))
        }
        "list" => {
            expect_no_args(&args)?;
            Ok(host_request("host.sandbox.list", json!({}), false))
        }
        "status" => {
            let Some(sandbox_id) = shift(&mut args) else {
                bail!("host sandboxes status requires SANDBOX_ID")
            };
            expect_no_args(&args)?;
            Ok(host_request_with_sandbox(
                "host.sandbox.status",
                json!({}),
                sandbox_id,
                false,
            ))
        }
        "release" => {
            let Some(sandbox_id) = shift(&mut args) else {
                bail!("host sandboxes release requires SANDBOX_ID")
            };
            expect_no_args(&args)?;
            Ok(host_request_with_sandbox(
                "host.sandbox.release",
                json!({}),
                sandbox_id,
                false,
            ))
        }
        other => bail!(
            "unknown host sandboxes subcommand {other:?}; expected acquire | list | status | release"
        ),
    }
}

fn request_from_host_traces(
    mut args: Vec<String>,
    options: &ClientOptions,
) -> Result<GatewayRequest> {
    let Some(subcommand) = shift(&mut args) else {
        bail!("missing host traces subcommand; expected list | show | verify")
    };
    match subcommand.as_str() {
        "list" => {
            let limit = take_optional_u64(&mut args, "--limit")?;
            expect_no_args(&args)?;
            let mut body = json!({});
            insert_optional(&mut body, "sandbox_id", options.sandbox_id.clone());
            if let Some(limit) = limit {
                body["limit"] = json!(limit);
            }
            Ok(host_request("host.trace.requests", body, true))
        }
        "show" => {
            let limit = take_optional_u64(&mut args, "--limit")?;
            let Some(trace_id) = shift(&mut args) else {
                bail!("host traces show requires TRACE_ID")
            };
            expect_no_args(&args)?;
            let mut body = json!({ "trace_id": trace_id });
            if let Some(limit) = limit {
                body["limit"] = json!(limit);
            }
            Ok(host_request("host.trace.show", body, true))
        }
        "verify" => {
            let trace_id = shift(&mut args);
            expect_no_args(&args)?;
            let mut body = json!({});
            insert_optional(&mut body, "trace_id", trace_id);
            Ok(host_request("host.trace.verify", body, true))
        }
        other => bail!("unknown host traces subcommand {other:?}; expected list | show | verify"),
    }
}

fn request_from_daemon_commands(
    mut args: Vec<String>,
    sandbox_id: String,
) -> Result<GatewayRequest> {
    let Some(subcommand) = shift(&mut args) else {
        bail!(
            "missing daemon commands subcommand; expected exec | stdin | poll | cancel | collect | count"
        )
    };
    match subcommand.as_str() {
        "exec" => {
            let caller_id = take_optional_flag(&mut args, "--caller-id")?;
            let layer_stack_root = layer_stack_root_or_default(&mut args)?;
            let timeout = take_optional_u64(&mut args, "--timeout")?;
            let yield_time_ms = take_optional_u64(&mut args, "--yield-time-ms")?;
            let cmd = command_string(args)?;
            let mut body = json!({ "cmd": cmd, "layer_stack_root": layer_stack_root });
            insert_optional(&mut body, "caller_id", caller_id);
            if let Some(timeout) = timeout {
                body["timeout"] = json!(timeout);
            }
            if let Some(yield_time_ms) = yield_time_ms {
                body["yield_time_ms"] = json!(yield_time_ms);
            }
            Ok(daemon_request("sandbox.command.exec", body, sandbox_id, false))
        }
        "stdin" => {
            let yield_time_ms = take_optional_u64(&mut args, "--yield-time-ms")?;
            let Some(command_id) = shift(&mut args) else {
                bail!("daemon commands stdin requires COMMAND_ID")
            };
            let chars = command_string(args)?;
            let mut body = json!({ "command_id": command_id, "chars": chars });
            if let Some(yield_time_ms) = yield_time_ms {
                body["yield_time_ms"] = json!(yield_time_ms);
            }
            Ok(daemon_request(
                "sandbox.command.write_stdin",
                body,
                sandbox_id,
                false,
            ))
        }
        "poll" => {
            let last_n_lines = take_optional_u64(&mut args, "--last-n-lines")?;
            let Some(command_id) = shift(&mut args) else {
                bail!("daemon commands poll requires COMMAND_ID")
            };
            expect_no_args(&args)?;
            let mut body = json!({ "command_id": command_id });
            if let Some(last_n_lines) = last_n_lines {
                body["last_n_lines"] = json!(last_n_lines);
            }
            Ok(daemon_request(
                "sandbox.command.poll",
                body,
                sandbox_id,
                false,
            ))
        }
        "cancel" => {
            let Some(command_id) = shift(&mut args) else {
                bail!("daemon commands cancel requires COMMAND_ID")
            };
            expect_no_args(&args)?;
            Ok(daemon_request(
                "sandbox.command.cancel",
                json!({ "command_id": command_id }),
                sandbox_id,
                false,
            ))
        }
        "collect" => {
            let caller_id = take_optional_flag(&mut args, "--caller-id")?;
            let mut body = json!({});
            insert_optional(&mut body, "caller_id", caller_id);
            if !args.is_empty() {
                body["command_ids"] = json!(args);
            }
            Ok(daemon_request(
                "sandbox.command.collect_completed",
                body,
                sandbox_id,
                false,
            ))
        }
        "count" => {
            let caller_id = take_optional_flag(&mut args, "--caller-id")?;
            expect_no_args(&args)?;
            let mut body = json!({});
            insert_optional(&mut body, "caller_id", caller_id);
            Ok(daemon_request(
                "sandbox.command.count",
                body,
                sandbox_id,
                false,
            ))
        }
        other => bail!(
            "unknown daemon commands subcommand {other:?}; expected exec | stdin | poll | cancel | collect | count"
        ),
    }
}

fn request_from_daemon_run(mut args: Vec<String>, sandbox_id: String) -> Result<GatewayRequest> {
    let Some(subcommand) = shift(&mut args) else {
        bail!("missing daemon run subcommand; expected end | cancel-all")
    };
    match subcommand.as_str() {
        "end" => {
            let caller_id = take_required_flag(&mut args, "--caller-id")?;
            let grace_s = take_optional_f64(&mut args, "--grace-s")?;
            expect_no_args(&args)?;
            let mut body = json!({ "caller_id": caller_id });
            if let Some(grace_s) = grace_s {
                body["grace_s"] = json!(grace_s);
            }
            Ok(daemon_request("sandbox.run.end", body, sandbox_id, false))
        }
        "cancel-all" => {
            let grace_s = take_optional_f64(&mut args, "--grace-s")?;
            expect_no_args(&args)?;
            let mut body = json!({});
            if let Some(grace_s) = grace_s {
                body["grace_s"] = json!(grace_s);
            }
            Ok(daemon_request(
                "sandbox.run.cancel_all",
                body,
                sandbox_id,
                true,
            ))
        }
        other => bail!("unknown daemon run subcommand {other:?}; expected end | cancel-all"),
    }
}

fn request_from_op(
    mut args: Vec<String>,
    sandbox_id: Option<String>,
    operator: bool,
) -> Result<GatewayRequest> {
    let Some(op) = shift(&mut args) else {
        bail!("op requires OP")
    };
    let body = match shift(&mut args) {
        Some(raw) => serde_json::from_str::<Value>(&raw)
            .with_context(|| format!("parse ARGS_JSON for {op}"))?,
        None => json!({}),
    };
    if !body.is_object() {
        bail!("ARGS_JSON must be a JSON object");
    }
    expect_no_args(&args)?;
    Ok(GatewayRequest {
        op,
        args: body,
        sandbox_id,
        operator,
    })
}

fn host_request(op: &str, args: Value, operator: bool) -> GatewayRequest {
    GatewayRequest {
        op: op.to_owned(),
        args,
        sandbox_id: None,
        operator,
    }
}

fn host_request_with_sandbox(
    op: &str,
    args: Value,
    sandbox_id: String,
    operator: bool,
) -> GatewayRequest {
    GatewayRequest {
        op: op.to_owned(),
        args,
        sandbox_id: Some(sandbox_id),
        operator,
    }
}

fn daemon_request(op: &str, args: Value, sandbox_id: String, operator: bool) -> GatewayRequest {
    GatewayRequest {
        op: op.to_owned(),
        args,
        sandbox_id: Some(sandbox_id),
        operator,
    }
}

fn send_and_print(request: GatewayRequest, options: &ClientOptions) -> Result<()> {
    let socket = if request.operator || options.operator {
        transport::operator_socket_path(&options.socket)
    } else {
        options.socket.clone()
    };
    let response = send_request(&socket, &request)?;
    print_response(response, options.envelope)
}

fn send_request(socket: &Path, request: &GatewayRequest) -> Result<Value> {
    let mut payload = json!({
        "op": request.op,
        "invocation_id": invocation_id(),
        "args": request.args,
    });
    if let Some(sandbox_id) = &request.sandbox_id {
        payload["sandbox_id"] = json!(sandbox_id);
    }
    let mut stream =
        UnixStream::connect(socket).with_context(|| format!("connect {}", socket.display()))?;
    let line = serde_json::to_vec(&payload).context("encode gateway request")?;
    stream.write_all(&line).context("write gateway request")?;
    stream
        .write_all(b"\n")
        .context("terminate gateway request")?;
    stream.flush().ok();

    let mut reader = BufReader::new(stream);
    let mut response = String::new();
    reader
        .read_line(&mut response)
        .context("read gateway response")?;
    if response.is_empty() {
        bail!("gateway closed without a response");
    }
    serde_json::from_str(response.trim_end()).context("decode gateway response")
}

fn print_response(response: Value, envelope: bool) -> Result<()> {
    if envelope {
        println!("{}", serde_json::to_string_pretty(&response)?);
        return Ok(());
    }
    match response.get("status").and_then(Value::as_str) {
        Some("ok" | "running" | "cancelled" | "timed_out") => {
            println!(
                "{}",
                serde_json::to_string_pretty(response.get("result").unwrap_or(&Value::Null))?
            );
            Ok(())
        }
        Some("error" | "rejected") => {
            let error = response.get("error").unwrap_or(&Value::Null);
            let kind = error
                .get("kind")
                .and_then(Value::as_str)
                .unwrap_or("gateway_error");
            let message = error
                .get("message")
                .and_then(Value::as_str)
                .unwrap_or("gateway request failed");
            bail!("{kind}: {message}")
        }
        Some(other) => bail!("unknown gateway response status {other:?}"),
        None => bail!("gateway response missing status"),
    }
}

fn invocation_id() -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    format!("cli-{}-{nanos}", std::process::id())
}

fn require_sandbox_id(options: &ClientOptions) -> Result<String> {
    options
        .sandbox_id
        .clone()
        .context("daemon commands require --sandbox-id SANDBOX_ID")
}

fn expect_no_args(args: &[String]) -> Result<()> {
    if args.is_empty() {
        Ok(())
    } else {
        bail!("unexpected arguments: {}", args.join(" "))
    }
}

fn shift(args: &mut Vec<String>) -> Option<String> {
    if args.is_empty() {
        None
    } else {
        Some(args.remove(0))
    }
}

fn command_string(mut args: Vec<String>) -> Result<String> {
    if args.first().is_some_and(|arg| arg == "--") {
        args.remove(0);
    }
    if args.is_empty() {
        bail!("command text is required");
    }
    Ok(args.join(" "))
}

fn take_optional_flag(args: &mut Vec<String>, flag: &str) -> Result<Option<String>> {
    let Some(index) = args.iter().position(|arg| arg == flag) else {
        return Ok(None);
    };
    Ok(Some(take_flag_value(args, index, flag)?))
}

fn take_optional_flag_any(args: &mut Vec<String>, flags: &[&str]) -> Result<Option<String>> {
    for flag in flags {
        if let Some(index) = args.iter().position(|arg| arg == flag) {
            return Ok(Some(take_flag_value(args, index, flag)?));
        }
    }
    Ok(None)
}

fn take_required_flag(args: &mut Vec<String>, flag: &str) -> Result<String> {
    take_optional_flag(args, flag)?.with_context(|| format!("{flag} is required"))
}

fn layer_stack_root_or_default(args: &mut Vec<String>) -> Result<String> {
    Ok(
        take_optional_flag_any(args, &["--layer-stack-root", "--layer_stack_root"])?
            .unwrap_or_else(|| DEFAULT_LAYER_STACK_ROOT.to_owned()),
    )
}

fn take_optional_u64(args: &mut Vec<String>, flag: &str) -> Result<Option<u64>> {
    take_optional_flag(args, flag)?
        .map(|value| value.parse().with_context(|| format!("parse {flag}")))
        .transpose()
}

fn take_optional_f64(args: &mut Vec<String>, flag: &str) -> Result<Option<f64>> {
    take_optional_flag(args, flag)?
        .map(|value| value.parse().with_context(|| format!("parse {flag}")))
        .transpose()
}

fn take_flag_value(args: &mut Vec<String>, index: usize, flag: &str) -> Result<String> {
    if index + 1 >= args.len() {
        bail!("{flag} requires a value");
    }
    args.remove(index);
    Ok(args.remove(index))
}

fn insert_optional(body: &mut Value, key: &str, value: Option<String>) {
    if let Some(value) = value {
        body[key] = json!(value);
    }
}
