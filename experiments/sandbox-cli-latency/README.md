# Sandbox CLI latency experiment

This experiment measures end-to-end wall-clock latency for `sandbox-cli`
subprocess invocations. It is intended for comparing CLI changes over time, not
for replacing daemon/runtime telemetry.

## Default safe run

The default cases cover local CLI/help paths and do not require a running
gateway or sandbox:

```sh
python3 experiments/sandbox-cli-latency/run.py --build --iterations 50 --warmups 10
```

Use `--build` when you want to measure the compiled `sandbox-cli` binary. To
measure the repo wrapper, including its `cargo run` overhead, pass it
explicitly:

```sh
python3 experiments/sandbox-cli-latency/run.py --cli bin/sandbox-cli --iterations 10 --warmups 2
```

Results are written under:

```text
target/experiments/sandbox-cli-latency/<timestamp>/
```

Each run writes:

- `summary.json` - per-case latency statistics and experiment metadata.
- `samples.csv` - one row per warmup or measured invocation.
- `samples.jsonl` - detailed per-invocation records with output hashes and
  short output samples.

## Gateway-backed commands

Start the gateway before measuring commands that cross the Unix socket:

```sh
export PATH="$PWD/bin:$PATH"
start-sandbox-gateway
python3 experiments/sandbox-cli-latency/run.py \
  --build \
  --commands-file experiments/sandbox-cli-latency/commands.example.json \
  --iterations 50 \
  --warmups 10
```

The example file includes `manager list_sandboxes`. For runtime probes, add a
case with a real sandbox id:

```json
[
  {
    "name": "runtime_exec_pwd",
    "args": ["--default-sandbox-id", "sbox-1", "runtime", "exec_command", "pwd"],
    "description": "Runtime command probe against an existing sandbox."
  }
]
```

## Manager create/destroy lifecycle

Use `--manager-lifecycle` to run a real gateway-backed manager lifecycle probe.
Each warmup or measured sample creates one sandbox, parses the returned `id`,
then destroys that same sandbox:

```sh
export PATH="$PWD/bin:$PATH"
start-sandbox-gateway
python3 experiments/sandbox-cli-latency/run.py \
  --build \
  --manager-lifecycle \
  --manager-lifecycle-image ubuntu:24.04 \
  --iterations 10 \
  --warmups 1 \
  --timeout 120
```

If the gateway is not using the default socket, pass it explicitly:

```sh
python3 experiments/sandbox-cli-latency/run.py \
  --build \
  --manager-lifecycle \
  --manager-lifecycle-gateway-socket /tmp/eos-gateway.sock
```

Per-sample workspace roots are created under the run output directory by
default. Use `--manager-lifecycle-workspace-base PATH` to place them elsewhere.
The sample JSONL rows include the created `sandbox_id`, `workspace_root`,
individual create/destroy durations, and the exact command list.

## Custom cases

For one-off cases, use `NAME::ARGS`:

```sh
python3 experiments/sandbox-cli-latency/run.py \
  --build \
  --case 'manager_help::manager help' \
  --case 'runtime_exec_pwd::--default-sandbox-id sbox-1 runtime exec_command pwd'
```

For repeatable experiments, prefer a JSON command file:

```json
[
  {
    "name": "manager_list_sandboxes",
    "args": ["manager", "list_sandboxes"],
    "description": "Gateway round trip through manager list."
  }
]
```

The reported latency is process-level elapsed time: CLI startup, argument
parsing, config discovery, gateway connection, request handling, response
rendering, and process exit. Runtime operation latency inside the daemon should
still be read from trace/telemetry data.
