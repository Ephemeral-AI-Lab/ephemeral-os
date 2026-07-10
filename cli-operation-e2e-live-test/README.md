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
├── conftest.py                # fixtures: gateway bring-up, sandbox lifecycle, session cleanup
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
├── config/                    # family: config (YAML knobs end to end; pytest -m config)
│   ├── conftest.py            # family gateway custody + baseline restore
│   ├── helpers.py             # make_config (generated YAML under pytest tmp), gateway_with_config
│   ├── test_daemon_reload.py  # Lane A: per-create daemon YAML reload + behavior knobs
│   ├── test_validation.py     # invalid config rejection on both lanes
│   ├── test_manager_section.py# Lane B: gateway-start manager.docker knobs
│   └── test_phase_knobs.py    # consolidation phases 1–3 (skip-marked until landed)
├── runtime/                   # placeholder (empty; tests not implemented yet)
└── observability/             # placeholder (see observability/README.md)
    └── test_observability.py
```

The `config` family owns the shared gateway while it runs (it restarts the
gateway against generated YAMLs, then restores the baseline `config/prd.yml`
gateway in its package finalizer), so it is serial — deselect it with
`-m "not config"` in parallel lanes and run it with `pytest -m config`.

Each **family** owns a folder with its own `helpers.py` (thin wrappers over the
family's `sandbox-cli` operations) and its `test_*.py`. `core/` holds only
generic, cross-family machinery. Sandbox lifecycle lives in `conftest.py`
fixtures so teardown runs even when a test fails.

`runtime/` is intentionally empty for now — when runtime tests are added they
follow the same per-family layout (e.g. `runtime/command/`,
`runtime/workspace_session/`).

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
pytest observability        # placeholder (skipped)
pytest                      # everything
```

Run a single family or test:

```sh
pytest manager/management
pytest manager/management/test_management.py::test_sandbox_lifecycle
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
| `E2E_WORKSPACE_VARIANT`       | `testbed`             | variant subfolder under `repo/` (host dir, bind-mounted as workspace root) |
| `E2E_WORKSPACE_ROOT`          | `repo/<variant>`      | absolute host workspace root (overrides the variant) |
| `SANDBOX_GATEWAY_CONFIG_YAML` | `../config/prd.yml`   | daemon/sandbox config YAML used by the gateway      |
| `E2E_REBUILD_BINARY`          | `1`                   | cold-start with `--rebuild-binary`; `0` to skip     |

```sh
E2E_IMAGE=debian:12 pytest manager                  # different image
E2E_WORKSPACE_VARIANT=special_case_b pytest manager # different repo/ workspace variant
E2E_REBUILD_BINARY=0 pytest -m smoke                # fastest cold start (no forced daemon rebuild)
```

Workspace variants live under `repo/` — one host directory per variant
(`repo/testbed`, `repo/special_case_b`, …), bind-mounted into the sandbox as its
workspace root. `repo/testbed` is the default; add a variant by creating a new
subfolder.

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
