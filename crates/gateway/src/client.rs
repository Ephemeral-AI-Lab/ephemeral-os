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

pub(crate) fn run_legacy(command: &str, argv: impl Iterator<Item = String>) -> Result<()> {
    let mut args = argv.collect::<Vec<_>>();
    let options = ClientOptions::parse(&mut args)?;
    let request = match command {
        "op" => request_from_op(args, options.sandbox_id.clone(), false)?,
        "images" => request_from_host_images(args)?,
        "containers" => request_from_host_containers(args, &options)?,
        "sandboxes" => request_from_host_sandboxes(args, &options)?,
        "image-profiles" | "profiles" => request_from_host_profiles(args)?,
        other => bail!("unknown client command {other:?}"),
    };
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
  sandbox-gateway host sandboxes acquire [--image-profile NAME]
  sandbox-gateway host sandboxes setup --sandbox-id SANDBOX_ID --workspace-root PATH
  sandbox-gateway host sandboxes list
  sandbox-gateway host sandboxes status SANDBOX_ID
  sandbox-gateway host sandboxes release SANDBOX_ID
  sandbox-gateway host traces list [--sandbox-id SANDBOX_ID] [--limit N]
  sandbox-gateway host traces show TRACE_ID [--limit N]
  sandbox-gateway host traces verify [TRACE_ID]
  sandbox-gateway host op OP [ARGS_JSON] [--sandbox-id SANDBOX_ID] [--operator]

  sandbox-gateway daemon --sandbox-id SANDBOX_ID ping
  sandbox-gateway daemon --sandbox-id SANDBOX_ID files read PATH
  sandbox-gateway daemon --sandbox-id SANDBOX_ID files write PATH --content TEXT
  sandbox-gateway daemon --sandbox-id SANDBOX_ID files edit PATH --old TEXT --new TEXT [--replace-all]
  sandbox-gateway daemon --sandbox-id SANDBOX_ID commands exec -- COMMAND
  sandbox-gateway daemon --sandbox-id SANDBOX_ID commands stdin COMMAND_ID TEXT
  sandbox-gateway daemon --sandbox-id SANDBOX_ID commands poll COMMAND_ID [--last-n-lines N]
  sandbox-gateway daemon --sandbox-id SANDBOX_ID commands cancel COMMAND_ID
  sandbox-gateway daemon --sandbox-id SANDBOX_ID commands collect [COMMAND_ID...]
  sandbox-gateway daemon --sandbox-id SANDBOX_ID commands count [--caller-id ID]
  sandbox-gateway daemon --sandbox-id SANDBOX_ID plugins list
  sandbox-gateway daemon --sandbox-id SANDBOX_ID plugins health
  sandbox-gateway daemon --sandbox-id SANDBOX_ID pyright symbols FILE [--query TEXT] [--workspace]
  sandbox-gateway daemon --sandbox-id SANDBOX_ID pyright definition FILE --line N --column N
  sandbox-gateway daemon --sandbox-id SANDBOX_ID pyright references FILE --line N --column N
  sandbox-gateway daemon --sandbox-id SANDBOX_ID pyright diagnostics FILE
  sandbox-gateway daemon --sandbox-id SANDBOX_ID isolation enter --caller-id ID
  sandbox-gateway daemon --sandbox-id SANDBOX_ID isolation status --caller-id ID
  sandbox-gateway daemon --sandbox-id SANDBOX_ID isolation exit --caller-id ID [--grace-s SECONDS]
  sandbox-gateway daemon --sandbox-id SANDBOX_ID checkpoint metrics
  sandbox-gateway daemon --sandbox-id SANDBOX_ID checkpoint binding
  sandbox-gateway daemon --sandbox-id SANDBOX_ID checkpoint ensure-base --workspace-root PATH
  sandbox-gateway daemon --sandbox-id SANDBOX_ID checkpoint build-base --workspace-root PATH [--reset]
  sandbox-gateway daemon --sandbox-id SANDBOX_ID checkpoint commit-workspace --workspace-root PATH
  sandbox-gateway daemon --sandbox-id SANDBOX_ID checkpoint commit-git --workspace-root PATH --message TEXT [PATH...]
  sandbox-gateway daemon --sandbox-id SANDBOX_ID run end --caller-id ID [--grace-s SECONDS]
  sandbox-gateway daemon --sandbox-id SANDBOX_ID run cancel-all [--grace-s SECONDS]
  sandbox-gateway daemon --sandbox-id SANDBOX_ID op OP [ARGS_JSON] [--operator]

common client flags:
  --socket PATH      gateway client socket; overrides EOS_GATEWAY_SOCKET/default
  --operator         connect to <socket>.operator
  --layer-stack-root PATH
                    daemon shortcut override; default /eos/layer-stack
  --envelope         print the full response envelope instead of result only"
    );
}

#[derive(Debug)]
struct ClientOptions {
    socket: PathBuf,
    operator: bool,
    envelope: bool,
    sandbox_id: Option<String>,
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
struct GatewayRequest {
    op: String,
    args: Value,
    sandbox_id: Option<String>,
    operator: bool,
}

fn request_from_host(mut args: Vec<String>, options: &ClientOptions) -> Result<GatewayRequest> {
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

fn request_from_daemon(mut args: Vec<String>, options: &ClientOptions) -> Result<GatewayRequest> {
    let Some(group) = shift(&mut args) else {
        bail!(
            "missing daemon command; expected ping | files | commands | plugins | pyright | isolation | checkpoint | run | op"
        )
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
        "files" => request_from_daemon_files(args, sandbox_id),
        "commands" => request_from_daemon_commands(args, sandbox_id),
        "plugins" => request_from_daemon_plugins(args, sandbox_id),
        "pyright" => request_from_daemon_pyright(args, sandbox_id),
        "isolation" => request_from_daemon_isolation(args, sandbox_id),
        "checkpoint" => request_from_daemon_checkpoint(args, sandbox_id),
        "run" => request_from_daemon_run(args, sandbox_id),
        "op" => request_from_op(args, Some(sandbox_id), false),
        other => bail!(
            "unknown daemon command {other:?}; expected ping | files | commands | plugins | pyright | isolation | checkpoint | run | op"
        ),
    }
}

fn request_from_host_profiles(args: Vec<String>) -> Result<GatewayRequest> {
    expect_args(&args, &["list"])?;
    Ok(host_request("host.image_profiles.list", json!({}), false))
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
    options: &ClientOptions,
) -> Result<GatewayRequest> {
    let Some(subcommand) = shift(&mut args) else {
        bail!(
            "missing host sandboxes subcommand; expected acquire | setup | list | status | release"
        )
    };
    match subcommand.as_str() {
        "acquire" => {
            let image_profile = take_optional_flag(&mut args, "--image-profile")?;
            expect_no_args(&args)?;
            let mut body = json!({});
            insert_optional(&mut body, "image_profile", image_profile);
            Ok(host_request("host.sandbox.acquire", body, false))
        }
        "setup" => {
            let sandbox_id = require_sandbox_id(options)?;
            let layer_stack_root = layer_stack_root_or_default(&mut args)?;
            let workspace_root = take_required_flag_any(
                &mut args,
                &["--workspace-root", "--workspace_root"],
                "--workspace-root",
            )?;
            expect_no_args(&args)?;
            Ok(daemon_request(
                "sandbox.checkpoint.ensure_base",
                json!({
                    "layer_stack_root": layer_stack_root,
                    "workspace_root": workspace_root,
                }),
                sandbox_id,
                true,
            ))
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
            "unknown host sandboxes subcommand {other:?}; expected acquire | setup | list | status | release"
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

fn request_from_daemon_files(mut args: Vec<String>, sandbox_id: String) -> Result<GatewayRequest> {
    let Some(subcommand) = shift(&mut args) else {
        bail!("missing daemon files subcommand; expected read | write | edit")
    };
    match subcommand.as_str() {
        "read" => {
            let caller_id = take_optional_flag(&mut args, "--caller-id")?;
            let layer_stack_root = layer_stack_root_or_default(&mut args)?;
            let Some(path) = shift(&mut args) else {
                bail!("daemon files read requires PATH")
            };
            expect_no_args(&args)?;
            let mut body = json!({ "path": path, "layer_stack_root": layer_stack_root });
            insert_optional(&mut body, "caller_id", caller_id);
            Ok(daemon_request("sandbox.file.read", body, sandbox_id, false))
        }
        "write" => {
            let caller_id = take_optional_flag(&mut args, "--caller-id")?;
            let layer_stack_root = layer_stack_root_or_default(&mut args)?;
            let content = take_optional_flag(&mut args, "--content")?;
            let overwrite = take_optional_bool(&mut args, "--overwrite")?;
            let Some(path) = shift(&mut args) else {
                bail!("daemon files write requires PATH")
            };
            expect_no_args(&args)?;
            let content = content.context("daemon files write requires --content TEXT")?;
            let mut body =
                json!({ "path": path, "content": content, "layer_stack_root": layer_stack_root });
            insert_optional(&mut body, "caller_id", caller_id);
            if let Some(overwrite) = overwrite {
                body["overwrite"] = json!(overwrite);
            }
            Ok(daemon_request(
                "sandbox.file.write",
                body,
                sandbox_id,
                false,
            ))
        }
        "edit" => {
            let caller_id = take_optional_flag(&mut args, "--caller-id")?;
            let layer_stack_root = layer_stack_root_or_default(&mut args)?;
            let edits_json = take_optional_flag(&mut args, "--edits-json")?;
            let old_text = take_optional_flag(&mut args, "--old")?;
            let new_text = take_optional_flag(&mut args, "--new")?;
            let replace_all = take_switch(&mut args, "--replace-all");
            let Some(path) = shift(&mut args) else {
                bail!("daemon files edit requires PATH")
            };
            expect_no_args(&args)?;
            let edits = match edits_json {
                Some(raw) => serde_json::from_str::<Value>(&raw).context("parse --edits-json")?,
                None => json!([{
                    "old_text": old_text.context("daemon files edit requires --old TEXT")?,
                    "new_text": new_text.context("daemon files edit requires --new TEXT")?,
                    "replace_all": replace_all,
                }]),
            };
            if !edits.is_array() {
                bail!("--edits-json must be a JSON array");
            }
            let mut body =
                json!({ "path": path, "edits": edits, "layer_stack_root": layer_stack_root });
            insert_optional(&mut body, "caller_id", caller_id);
            Ok(daemon_request("sandbox.file.edit", body, sandbox_id, false))
        }
        other => bail!("unknown daemon files subcommand {other:?}; expected read | write | edit"),
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

fn request_from_daemon_plugins(
    mut args: Vec<String>,
    sandbox_id: String,
) -> Result<GatewayRequest> {
    let Some(subcommand) = shift(&mut args) else {
        bail!("missing daemon plugins subcommand; expected list | health")
    };
    match subcommand.as_str() {
        "list" => {
            let caller_id = take_optional_flag(&mut args, "--caller-id")?;
            expect_no_args(&args)?;
            let mut body = json!({});
            insert_optional(&mut body, "caller_id", caller_id);
            Ok(daemon_request(
                "sandbox.plugin.list",
                body,
                sandbox_id,
                false,
            ))
        }
        "health" => {
            let caller_id = take_optional_flag(&mut args, "--caller-id")?;
            let layer_stack_root = layer_stack_root_or_default(&mut args)?;
            expect_no_args(&args)?;
            let mut body = json!({ "layer_stack_root": layer_stack_root });
            insert_optional(&mut body, "caller_id", caller_id);
            Ok(daemon_request(
                "sandbox.plugin.health",
                body,
                sandbox_id,
                false,
            ))
        }
        other => bail!("unknown daemon plugins subcommand {other:?}; expected list | health"),
    }
}

fn request_from_daemon_pyright(
    mut args: Vec<String>,
    sandbox_id: String,
) -> Result<GatewayRequest> {
    let Some(subcommand) = shift(&mut args) else {
        bail!("missing daemon pyright subcommand; expected symbols | definition | references | diagnostics")
    };
    match subcommand.as_str() {
        "symbols" => {
            let common = PyrightCommon::parse(&mut args)?;
            let query = take_optional_flag(&mut args, "--query")?;
            let workspace = take_switch(&mut args, "--workspace");
            let Some(file_path) = shift(&mut args) else {
                bail!("daemon pyright symbols requires FILE")
            };
            expect_no_args(&args)?;
            let mut body = common.body(file_path);
            insert_optional(&mut body, "query", query);
            if workspace {
                body["workspace"] = json!(true);
            }
            Ok(daemon_request(
                "sandbox.plugin.pyright_lsp.query_symbols",
                body,
                sandbox_id,
                false,
            ))
        }
        "definition" => {
            let common = PyrightCommon::parse(&mut args)?;
            let position = take_position(&mut args)?;
            let Some(file_path) = shift(&mut args) else {
                bail!("daemon pyright definition requires FILE")
            };
            expect_no_args(&args)?;
            let mut body = common.body(file_path);
            body["position"] = position;
            Ok(daemon_request(
                "sandbox.plugin.pyright_lsp.definition",
                body,
                sandbox_id,
                false,
            ))
        }
        "references" => {
            let common = PyrightCommon::parse(&mut args)?;
            let position = take_position(&mut args)?;
            let include_declaration = take_optional_bool(&mut args, "--include-declaration")?;
            let Some(file_path) = shift(&mut args) else {
                bail!("daemon pyright references requires FILE")
            };
            expect_no_args(&args)?;
            let mut body = common.body(file_path);
            body["position"] = position;
            if let Some(include_declaration) = include_declaration {
                body["include_declaration"] = json!(include_declaration);
            }
            Ok(daemon_request(
                "sandbox.plugin.pyright_lsp.references",
                body,
                sandbox_id,
                false,
            ))
        }
        "diagnostics" => {
            let common = PyrightCommon::parse(&mut args)?;
            let Some(file_path) = shift(&mut args) else {
                bail!("daemon pyright diagnostics requires FILE")
            };
            expect_no_args(&args)?;
            Ok(daemon_request(
                "sandbox.plugin.pyright_lsp.diagnostics",
                common.body(file_path),
                sandbox_id,
                false,
            ))
        }
        other => bail!(
            "unknown daemon pyright subcommand {other:?}; expected symbols | definition | references | diagnostics"
        ),
    }
}

struct PyrightCommon {
    caller_id: Option<String>,
    layer_stack_root: String,
}

impl PyrightCommon {
    fn parse(args: &mut Vec<String>) -> Result<Self> {
        let caller_id = take_optional_flag(args, "--caller-id")?;
        let layer_stack_root = layer_stack_root_or_default(args)?;
        Ok(Self {
            caller_id,
            layer_stack_root,
        })
    }

    fn body(&self, file_path: String) -> Value {
        let mut body = json!({
            "layer_stack_root": self.layer_stack_root.clone(),
            "file_path": file_path,
        });
        insert_optional(&mut body, "caller_id", self.caller_id.clone());
        body
    }
}

fn request_from_daemon_isolation(
    mut args: Vec<String>,
    sandbox_id: String,
) -> Result<GatewayRequest> {
    let Some(subcommand) = shift(&mut args) else {
        bail!("missing daemon isolation subcommand; expected enter | status | exit | list-open")
    };
    match subcommand.as_str() {
        "enter" => {
            let caller_id = take_required_flag(&mut args, "--caller-id")?;
            let layer_stack_root = layer_stack_root_or_default(&mut args)?;
            expect_no_args(&args)?;
            Ok(daemon_request(
                "sandbox.isolation.enter",
                json!({ "caller_id": caller_id, "layer_stack_root": layer_stack_root }),
                sandbox_id,
                false,
            ))
        }
        "status" => {
            let caller_id = take_required_flag(&mut args, "--caller-id")?;
            expect_no_args(&args)?;
            Ok(daemon_request(
                "sandbox.isolation.status",
                json!({ "caller_id": caller_id }),
                sandbox_id,
                false,
            ))
        }
        "exit" => {
            let caller_id = take_required_flag(&mut args, "--caller-id")?;
            let grace_s = take_optional_f64(&mut args, "--grace-s")?;
            expect_no_args(&args)?;
            let mut body = json!({ "caller_id": caller_id });
            if let Some(grace_s) = grace_s {
                body["grace_s"] = json!(grace_s);
            }
            Ok(daemon_request(
                "sandbox.isolation.exit",
                body,
                sandbox_id,
                false,
            ))
        }
        "list-open" => {
            expect_no_args(&args)?;
            Ok(daemon_request(
                "sandbox.isolation.list_open",
                json!({}),
                sandbox_id,
                true,
            ))
        }
        other => bail!(
            "unknown daemon isolation subcommand {other:?}; expected enter | status | exit | list-open"
        ),
    }
}

fn request_from_daemon_checkpoint(
    mut args: Vec<String>,
    sandbox_id: String,
) -> Result<GatewayRequest> {
    let Some(subcommand) = shift(&mut args) else {
        bail!(
            "missing daemon checkpoint subcommand; expected metrics | binding | ensure-base | build-base | commit-workspace | commit-git"
        )
    };
    match subcommand.as_str() {
        "metrics" => {
            let layer_stack_root = layer_stack_root_or_default(&mut args)?;
            expect_no_args(&args)?;
            Ok(daemon_request(
                "sandbox.checkpoint.layer_metrics",
                json!({ "layer_stack_root": layer_stack_root }),
                sandbox_id,
                true,
            ))
        }
        "binding" => {
            let layer_stack_root = layer_stack_root_or_default(&mut args)?;
            expect_no_args(&args)?;
            Ok(daemon_request(
                "sandbox.checkpoint.binding",
                json!({ "layer_stack_root": layer_stack_root }),
                sandbox_id,
                true,
            ))
        }
        "ensure-base" => checkpoint_workspace_request(
            "sandbox.checkpoint.ensure_base",
            args,
            sandbox_id,
            true,
        ),
        "build-base" => {
            let reset = take_switch(&mut args, "--reset");
            let mut request = checkpoint_workspace_request(
                "sandbox.checkpoint.build_base",
                args,
                sandbox_id,
                true,
            )?;
            if reset {
                request.args["reset"] = json!(true);
            }
            Ok(request)
        }
        "commit-workspace" => checkpoint_workspace_request(
            "sandbox.checkpoint.commit_to_workspace",
            args,
            sandbox_id,
            true,
        ),
        "commit-git" => {
            let layer_stack_root = layer_stack_root_or_default(&mut args)?;
            let workspace_root = workspace_root_required(&mut args)?;
            let message = take_required_flag(&mut args, "--message")?;
            let paths = args;
            let mut body = json!({
                "layer_stack_root": layer_stack_root,
                "workspace_root": workspace_root,
                "message": message,
            });
            if !paths.is_empty() {
                body["paths"] = json!(paths);
            }
            Ok(daemon_request(
                "sandbox.checkpoint.commit_to_git",
                body,
                sandbox_id,
                true,
            ))
        }
        other => bail!(
            "unknown daemon checkpoint subcommand {other:?}; expected metrics | binding | ensure-base | build-base | commit-workspace | commit-git"
        ),
    }
}

fn checkpoint_workspace_request(
    op: &str,
    mut args: Vec<String>,
    sandbox_id: String,
    operator: bool,
) -> Result<GatewayRequest> {
    let layer_stack_root = layer_stack_root_or_default(&mut args)?;
    let workspace_root = workspace_root_required(&mut args)?;
    expect_no_args(&args)?;
    Ok(daemon_request(
        op,
        json!({ "layer_stack_root": layer_stack_root, "workspace_root": workspace_root }),
        sandbox_id,
        operator,
    ))
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

fn expect_args(actual: &[String], expected: &[&str]) -> Result<()> {
    if actual == expected {
        Ok(())
    } else {
        bail!("expected {}", expected.join(" "))
    }
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

fn take_switch(args: &mut Vec<String>, flag: &str) -> bool {
    if let Some(index) = args.iter().position(|arg| arg == flag) {
        args.remove(index);
        true
    } else {
        false
    }
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

fn take_required_flag_any(
    args: &mut Vec<String>,
    flags: &[&str],
    canonical: &str,
) -> Result<String> {
    take_optional_flag_any(args, flags)?.with_context(|| format!("{canonical} is required"))
}

fn layer_stack_root_or_default(args: &mut Vec<String>) -> Result<String> {
    Ok(
        take_optional_flag_any(args, &["--layer-stack-root", "--layer_stack_root"])?
            .unwrap_or_else(|| DEFAULT_LAYER_STACK_ROOT.to_owned()),
    )
}

fn workspace_root_required(args: &mut Vec<String>) -> Result<String> {
    take_required_flag_any(
        args,
        &["--workspace-root", "--workspace_root"],
        "--workspace-root",
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

fn take_optional_bool(args: &mut Vec<String>, flag: &str) -> Result<Option<bool>> {
    take_optional_flag(args, flag)?
        .map(|value| match value.as_str() {
            "true" | "1" | "yes" => Ok(true),
            "false" | "0" | "no" => Ok(false),
            _ => bail!("{flag} must be true or false"),
        })
        .transpose()
}

fn take_position(args: &mut Vec<String>) -> Result<Value> {
    let line = take_required_flag(args, "--line")?
        .parse::<u64>()
        .context("parse --line")?;
    let character = take_required_flag(args, "--column")?
        .parse::<u64>()
        .context("parse --column")?;
    Ok(json!({
        "line": line,
        "character": character,
    }))
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

#[cfg(test)]
mod tests {
    use super::*;

    fn options() -> ClientOptions {
        ClientOptions {
            socket: PathBuf::from("/tmp/test.sock"),
            operator: false,
            envelope: false,
            sandbox_id: None,
        }
    }

    fn daemon_options() -> ClientOptions {
        ClientOptions {
            sandbox_id: Some("sb-1".to_owned()),
            ..options()
        }
    }

    #[test]
    fn host_image_list_routes_to_operator() -> Result<()> {
        let request = request_from_host(vec!["images".to_owned(), "list".to_owned()], &options())?;

        assert_eq!(request.op, "host.image.list");
        assert!(request.operator);
        Ok(())
    }

    #[test]
    fn host_container_stop_can_target_sandbox_id() -> Result<()> {
        let request = request_from_host(
            vec!["containers".to_owned(), "stop".to_owned()],
            &ClientOptions {
                sandbox_id: Some("sb-stop".to_owned()),
                ..options()
            },
        )?;

        assert_eq!(request.op, "host.container.stop");
        assert!(request.operator);
        assert_eq!(request.args["sandbox_id"], json!("sb-stop"));
        Ok(())
    }

    #[test]
    fn host_sandbox_setup_uses_default_layer_stack_and_workspace_alias() -> Result<()> {
        let request = request_from_host(
            vec![
                "sandboxes".to_owned(),
                "setup".to_owned(),
                "--workspace_root".to_owned(),
                "/testbed".to_owned(),
            ],
            &daemon_options(),
        )?;

        assert_eq!(request.op, "sandbox.checkpoint.ensure_base");
        assert_eq!(request.sandbox_id.as_deref(), Some("sb-1"));
        assert_eq!(request.args["layer_stack_root"], json!("/eos/layer-stack"));
        assert_eq!(request.args["workspace_root"], json!("/testbed"));
        assert!(request.operator);
        Ok(())
    }

    #[test]
    fn daemon_command_exec_defaults_layer_stack_root() -> Result<()> {
        let request = request_from_daemon(
            vec![
                "commands".to_owned(),
                "exec".to_owned(),
                "--".to_owned(),
                "pwd".to_owned(),
            ],
            &daemon_options(),
        )?;

        assert_eq!(request.op, "sandbox.command.exec");
        assert_eq!(request.sandbox_id.as_deref(), Some("sb-1"));
        assert_eq!(request.args["cmd"], json!("pwd"));
        assert_eq!(request.args["layer_stack_root"], json!("/eos/layer-stack"));
        assert!(!request.operator);
        Ok(())
    }

    #[test]
    fn daemon_pyright_definition_maps_column_to_character() -> Result<()> {
        let request = request_from_daemon(
            vec![
                "pyright".to_owned(),
                "definition".to_owned(),
                "--line".to_owned(),
                "3".to_owned(),
                "--column".to_owned(),
                "7".to_owned(),
                "src/main.py".to_owned(),
            ],
            &daemon_options(),
        )?;

        assert_eq!(request.op, "sandbox.plugin.pyright_lsp.definition");
        assert_eq!(request.args["layer_stack_root"], json!("/eos/layer-stack"));
        assert_eq!(request.args["position"]["line"], json!(3));
        assert_eq!(request.args["position"]["character"], json!(7));
        Ok(())
    }

    #[test]
    fn generic_daemon_op_accepts_json_args_and_sandbox() -> Result<()> {
        let request = request_from_daemon(
            vec![
                "op".to_owned(),
                "sandbox.file.read".to_owned(),
                r#"{"path":"README.md"}"#.to_owned(),
            ],
            &daemon_options(),
        )?;

        assert_eq!(request.op, "sandbox.file.read");
        assert_eq!(request.sandbox_id.as_deref(), Some("sb-1"));
        assert_eq!(request.args["path"], json!("README.md"));
        Ok(())
    }
}
