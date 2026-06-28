# cli-operation-e2e-live-test

A live, Docker-backed end-to-end suite that exercises the real
`sandbox-cli → gateway → manager → daemon → runtime` path against actual
containers. It is a **skeleton**: minimal, CLI-driven, and easy to extend.

Built with **pytest**. Every operation goes through `sandbox-cli`; the whole
lifecycle — setup, execution, observation, cleanup — is CLI-driven. Verification
reads the **structured JSON** each operation returns (`json.loads`); the suite
never scrapes `/tmp/eos-gateway.log`.

## Layout

```
cli-operation-e2e-live-test/
├── conftest.py                # fixtures: gateway bring-up, sandbox / workspace-session lifecycle
├── pytest.ini                 # pytest config (pythonpath, markers)
├── requirements.txt           # pytest
├── test_smoke.py              # smallest check (pytest -m smoke)
├── core/
│   ├── config.py              # customization knobs + resolved paths
│   ├── cli.py                 # sandbox-cli wrapper -> parsed JSON
│   └── gateway.py             # gateway_up (reuse running, else start sh script)
├── manager/                   # one folder per family
│   └── management/            # family: management
│       ├── helpers.py
│       └── test_management.py # create -> inspect -> list -> destroy
├── runtime/                   # one folder per family
│   ├── command/               # family: command
│   │   ├── helpers.py
│   │   └── test_command.py    # exec in session + one-shot
│   └── workspace_session/     # family: workspace_session
│       ├── helpers.py
│       └── test_workspace_session.py
└── observability/             # placeholder (see observability/README.md)
    └── test_observability.py
```

Each **family** owns a folder with its own `helpers.py` (thin wrappers over the
family's `sandbox-cli` operations) and its `test_*.py`. `core/` holds only
generic, cross-family machinery. Sandbox / workspace-session lifecycle lives in
`conftest.py` fixtures so teardown runs even when a test fails.

## Prerequisites

- Docker running locally (`docker version` must succeed).
- Python 3.9+ and pytest: `pip install -r requirements.txt`.
- A Rust toolchain (the gateway start script builds `sandbox-gateway` /
  `sandbox-cli`, and on cold start may cross-compile the in-container daemon).

## Running

```sh
cd cli-operation-e2e-live-test

pytest -m smoke              # smallest check: gateway up + structured list_sandboxes
pytest manager              # management lifecycle: create -> inspect -> list -> destroy
pytest runtime              # workspace_session + command families
pytest observability        # placeholder (skipped)
pytest                      # everything
```

Run a single family or test:

```sh
pytest runtime/command
pytest runtime/command/test_command.py::test_exec_one_shot
```

## Gateway lifecycle

The session-scoped autouse fixture `gateway_up` (→ `core/gateway.ensure_up`) is
idempotent:

- If a gateway already answers `manager list_sandboxes`, it is reused.
- Otherwise it runs `bin/start-sandbox-docker-gateway` (with `--rebuild-binary`
  when `E2E_REBUILD_BINARY=1`, the documented bring-up path), then polls until
  the gateway answers.

The start script daemonizes the gateway and writes `/tmp/eos-gateway.{pid,token,log}`;
`bin/sandbox-cli` auto-reads the token. The suite leaves the gateway running
between runs for fast iteration — only the sandboxes/sessions it creates are torn
down (by fixture teardown).

## Customization

All knobs live in `core/config.py` and are overridable from the environment:

| Variable                      | Default               | What it controls                                   |
|-------------------------------|-----------------------|----------------------------------------------------|
| `E2E_IMAGE`                   | `ubuntu:24.04`        | Docker image for `create_sandbox --image`          |
| `E2E_WORKSPACE_ROOT`          | `/testbed`            | `create_sandbox --workspace-root` (container path) |
| `E2E_NETWORK_PROFILE`         | `shared`              | workspace-session profile (`shared` \| `isolated`) |
| `SANDBOX_GATEWAY_CONFIG_YAML` | `../config/prd.yml`   | daemon/sandbox config YAML used by the gateway      |
| `E2E_REBUILD_BINARY`          | `1`                   | cold-start with `--rebuild-binary`; `0` to skip     |

```sh
E2E_IMAGE=debian:12 E2E_WORKSPACE_ROOT=/work pytest manager
E2E_REBUILD_BINARY=0 pytest -m smoke      # fastest cold start (no forced daemon rebuild)
```

## Why no log scraping

State and results are read from each operation's JSON output, not from gateway
or daemon logs. Structured observability (`observability snapshot`,
`manager get_observability_tree`) is the intended source for richer state
checks; see `observability/README.md`.

## Extending

- **New operation in an existing family** → add a wrapper to that family's
  `helpers.py` and a test to its `test_*.py`.
- **New family** → add `<domain>/<family>/{__init__.py,helpers.py,test_*.py}`.
  pytest discovers it automatically.
- **Shared machinery / fixtures** → add to `core/` or `conftest.py` only when it
  is family-agnostic.
