#!/usr/bin/env bash
# Idempotent Node 22 + Pyright install for the LSP plugin.
#
# Installs Node from the official tarball instead of conda/nodeenv. This has
# been reliable on Daytona sandboxes and works with Node 22+.

set -eu

PLUGIN_DIR="${EOS_PLUGIN_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)}"
NODE_HOME="${EOS_NODE_HOME:-/tmp/eos-node22}"
NODE_VERSION="${EOS_NODE_VERSION:-22.13.1}"
MARKER="$PLUGIN_DIR/.pyright_installed"

export PATH="$NODE_HOME/bin:$PATH"

if [ -f "$MARKER" ] && command -v pyright-langserver >/dev/null 2>&1; then
    exit 0
fi

use_python_fallback() {
    if [ "${EOS_LSP_ALLOW_PYTHON_FALLBACK:-0}" != "1" ]; then
        return 1
    fi
    echo "pyright-langserver unavailable; using Python LSP fallback" >&2
    mkdir -p "$PLUGIN_DIR"
    : > "$PLUGIN_DIR/.python_lsp_fallback"
    exit 0
}

download_node() {
    arch="$(uname -m)"
    case "$arch" in
        x86_64) node_arch=x64 ;;
        aarch64|arm64) node_arch=arm64 ;;
        *) echo "unsupported arch: $arch" >&2; return 2 ;;
    esac

    archive="node-v${NODE_VERSION}-linux-${node_arch}.tar.xz"
    urls="${EOS_NODE_DOWNLOAD_URLS:-https://nodejs.org/dist/v${NODE_VERSION}/${archive} https://registry.npmmirror.com/-/binary/node/v${NODE_VERSION}/${archive}}"
    mkdir -p "$NODE_HOME"
    cd "$NODE_HOME"
    if [ -n "${EOS_NODE_ARCHIVE:-}" ]; then
        cp "$EOS_NODE_ARCHIVE" node.tar.xz
        tar -xJf node.tar.xz --strip-components=1
        return 0
    fi
    for url in $urls; do
        rm -f node.tar.xz
        if curl -fL --retry 2 --connect-timeout 10 --max-time 240 "$url" -o node.tar.xz; then
            break
        fi
        echo "node download failed from $url" >&2
    done
    if [ ! -s node.tar.xz ]; then
        echo "failed to download Node ${NODE_VERSION}" >&2
        return 35
    fi
    tar -xJf node.tar.xz --strip-components=1
}

if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
    if [ -z "${EOS_NODE_ARCHIVE:-}" ] && [ "${EOS_LSP_ALLOW_DOWNLOAD:-0}" != "1" ]; then
        use_python_fallback || exit 35
    fi
    download_node || use_python_fallback || exit $?
fi

export PATH="$NODE_HOME/bin:$PATH"
npm config set prefix "$NODE_HOME"
if ! command -v pyright-langserver >/dev/null 2>&1; then
    npm install -g pyright || npm --registry=https://registry.npmmirror.com install -g pyright || use_python_fallback || exit $?
fi

node -v
npm -v
pyright --version
command -v pyright-langserver >/dev/null

mkdir -p "$(dirname "$MARKER")"
: > "$MARKER"
