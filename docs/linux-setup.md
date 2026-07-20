# Linux Setup

This guide runs the Ephemeral Sandbox core gateway and Docker-backed sandboxes
on Linux amd64. Docker must already be installed and reachable.

Binary releases do not require Rust, Cargo, Zig, or `cargo-zigbuild`. Source
builds require Rust/Cargo; the launcher prepares the Linux musl daemon toolchain
when a rebuild is requested.

## Binary Release

Download and start the Linux amd64 release:

```sh
curl -LO https://github.com/Ephemeral-AI-Lab/ephemeral-sandbox/releases/latest/download/ephemeral-sandbox-linux-amd64.tar.gz
tar -xzf ephemeral-sandbox-linux-amd64.tar.gz
cd ephemeral-sandbox-linux-amd64

export PATH="$PWD/bin:$PATH"
nohup bin/start-sandbox-linux-docker-gateway >/tmp/eos-gateway.log 2>&1 &
tail -f /tmp/eos-gateway.log /tmp/eos-gateway-linux.log
```

The launcher writes the gateway token to:

```text
$HOME/.ephemeral-sandbox/gateway.token
```

## Source Checkout

From a cloned checkout, start the Linux Docker gateway and rebuild the packaged
Linux daemon:

```sh
nohup bin/start-sandbox-linux-docker-gateway --rebuild-binary >/tmp/eos-linux-setup.log 2>&1 &
tail -f /tmp/eos-linux-setup.log /tmp/eos-gateway-linux.log
```

Use source builds for development or when testing local changes. The release
archive is the recommended path for users who only want to run sandboxes.

## Verify The CLIs

Use a small smoke workspace first. Binding a checkout with a large `target`
directory can make shared-base creation slow.

```sh
export PATH="$PWD/bin:$PATH"
export SANDBOX_GATEWAY_SOCKET=127.0.0.1:7878
export SANDBOX_GATEWAY_AUTH_TOKEN="$(cat "$HOME/.ephemeral-sandbox/gateway.token")"
export SANDBOX_IMAGE="${SANDBOX_IMAGE:?set SANDBOX_IMAGE to a Docker image already available to Docker}"

smoke=/tmp/ephemeral-sandbox-linux-smoke
rm -rf "$smoke"
mkdir -p "$smoke/src"
printf 'hello from Linux smoke workspace\n' >"$smoke/README.txt"
printf 'sandbox smoke file\n' >"$smoke/src/main.txt"

sandbox-manager-cli list_docker_images

created=$(
  sandbox-manager-cli create_sandbox \
    --image "$SANDBOX_IMAGE" \
    --workspace-bind-root "$smoke"
)
sandbox_id=$(printf '%s\n' "$created" | sed -n 's/.*"id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')

sandbox-runtime-cli --sandbox-id "$sandbox_id" exec_command 'pwd && cat README.txt && ls src'
sandbox-observability-cli snapshot --sandbox-id "$sandbox_id"
sandbox-manager-cli destroy_sandbox --sandbox-id "$sandbox_id"
```

Expected command output includes `/workspace`, `hello from Linux smoke
workspace`, and `main.txt`.

## Maintainer Release Package

Maintainers can build the Linux amd64 release archive from a source checkout:

```sh
bin/package-linux-amd64-release
```

The package script writes:

```text
dist/release/ephemeral-sandbox-linux-amd64.tar.gz
dist/release/ephemeral-sandbox-linux-amd64.tar.gz.sha256
```

## Troubleshooting

- `docker daemon is not reachable`: start Docker or fix the current user's
  Docker permissions.
- `cargo not found`: use the binary release, or install Rust through rustup for
  source builds.
- Gateway startup is slow on the first source build: the rebuild path compiles
  host binaries and prepares the musl daemon package.
- Sandbox creation is slow: use a small smoke workspace before binding a large
  project checkout.
