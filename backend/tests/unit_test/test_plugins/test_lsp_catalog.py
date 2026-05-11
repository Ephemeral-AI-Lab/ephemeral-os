"""Unit tests for the LSP plugin catalog (manifest + tool files)."""

from __future__ import annotations

import sys
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from plugins.core import loader as loader_mod
from plugins.core.discovery import discover_plugins
from plugins.core.loader import register_plugin_tools
from plugins.core.manifest import parse_plugin_manifest


_LSP_DIR = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "plugins"
    / "catalog"
    / "lsp"
)


@pytest.fixture(autouse=True)
def _isolate_loader() -> Iterator[None]:
    loader_mod._LOAD_CACHE.clear()
    pre = {
        name for name in sys.modules if name.startswith("plugins.catalog.")
    }
    yield
    loader_mod._LOAD_CACHE.clear()
    for name in [
        n
        for n in list(sys.modules)
        if n.startswith("plugins.catalog.") and n not in pre
    ]:
        sys.modules.pop(name, None)


def test_lsp_manifest_parses() -> None:
    manifest = parse_plugin_manifest(_LSP_DIR)
    assert manifest.name == "lsp"
    tool_names = sorted(t.name for t in manifest.tools)
    assert tool_names == [
        "lsp.diagnostics",
        "lsp.find_definitions",
        "lsp.find_references",
        "lsp.hover",
        "lsp.query_symbols",
    ]
    assert manifest.setup is not None
    assert manifest.setup.name == "setup.sh"
    assert manifest.runtime is not None
    assert manifest.runtime.name == "server.py"


def test_lsp_discovery_picks_up_the_plugin() -> None:
    catalog_dir = _LSP_DIR.parent
    plugins = discover_plugins(catalog_dir)
    assert any(m.name == "lsp" for m in plugins)


def test_register_plugin_tools_yields_five_lsp_tools() -> None:
    catalog_dir = _LSP_DIR.parent
    tools = register_plugin_tools(catalog_dir)
    lsp_tools = sorted(t.name for t in tools if t.name.startswith("lsp."))
    assert lsp_tools == [
        "lsp.diagnostics",
        "lsp.find_definitions",
        "lsp.find_references",
        "lsp.hover",
        "lsp.query_symbols",
    ]


def test_each_lsp_tool_creatable_via_factory() -> None:
    """Round-trip: register_plugin_tools → tools.factory → create_tool."""
    catalog_dir = _LSP_DIR.parent
    from tools.factory import (
        ToolFactoryContext,
        _register_many,
        create_tool,
    )

    _register_many(register_plugin_tools(catalog_dir))
    for name in (
        "lsp.hover",
        "lsp.find_definitions",
        "lsp.find_references",
        "lsp.diagnostics",
        "lsp.query_symbols",
    ):
        instance = create_tool(name, ToolFactoryContext())
        assert instance.name == name


def test_lsp_tool_modules_do_not_import_sandbox_internals() -> None:
    """Plugin tools must only import sandbox.* through sandbox.plugin."""
    forbidden_prefixes = (
        "sandbox.runtime",
        "sandbox.layer_stack",
        "sandbox.host",
        "sandbox.provider",
        "sandbox.api",  # tools should not import sandbox.api directly
    )
    for path in (_LSP_DIR / "tools").glob("*.py"):
        if path.name == "__init__.py":
            continue
        text = path.read_text(encoding="utf-8")
        for prefix in forbidden_prefixes:
            assert (
                f"from {prefix}" not in text and f"import {prefix}" not in text
            ), f"{path.name} imports forbidden {prefix}"


def test_lsp_setup_script_self_locates_and_installs_pyright(
    tmp_path: Path,
) -> None:
    plugin_dir = tmp_path / "lsp"
    plugin_dir.mkdir()
    (plugin_dir / "setup.sh").write_text(
        (_LSP_DIR / "setup.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_node_home = tmp_path / "node"
    fake_pyright_package = tmp_path / "pyright-1.1.409.tgz"
    fake_pyright_package.write_bytes(b"fake pyright package")
    log_path = tmp_path / "npm.log"
    (fake_bin / "node").write_text(
        """#!/usr/bin/env bash
set -eu
printf 'v22.13.1\n'
""",
        encoding="utf-8",
    )
    (fake_bin / "node").chmod(0o755)
    (fake_bin / "npm").write_text(
        """#!/usr/bin/env bash
set -eu
printf '%s\n' "$*" >> "$PYRIGHT_SETUP_LOG"
if [ "${1:-}" = "-v" ]; then
    printf '10.9.2\n'
    exit 0
fi
if [ "${1:-}" = "config" ] && [ "${2:-}" = "set" ]; then
    exit 0
fi
if [ "${1:-}" = "install" ]; then
    mkdir -p "$EOS_NODE_HOME/bin"
    printf '#!/usr/bin/env sh\nprintf "pyright 1.1.409\\n"\n' > "$EOS_NODE_HOME/bin/pyright"
    printf '#!/usr/bin/env sh\nexit 0\n' > "$EOS_NODE_HOME/bin/pyright-langserver"
    chmod +x "$EOS_NODE_HOME/bin/pyright" "$EOS_NODE_HOME/bin/pyright-langserver"
    exit 0
fi
printf 'unexpected npm args: %s\n' "$*" >&2
exit 99
""",
        encoding="utf-8",
    )
    (fake_bin / "npm").chmod(0o755)

    env = {
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "EOS_NODE_HOME": str(fake_node_home),
        "EOS_PYRIGHT_PACKAGE": str(fake_pyright_package),
        "PYRIGHT_SETUP_LOG": str(log_path),
    }
    completed = subprocess.run(
        ["bash", str(plugin_dir / "setup.sh")],
        cwd=plugin_dir,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert (plugin_dir / ".pyright_installed").is_file()
    npm_calls = log_path.read_text(encoding="utf-8").splitlines()
    assert "config set prefix " + str(fake_node_home) in npm_calls
    assert f"install -g --omit=optional {fake_pyright_package}" in npm_calls


def test_lsp_setup_script_falls_back_to_second_node_download_url(
    tmp_path: Path,
) -> None:
    plugin_dir = tmp_path / "lsp"
    plugin_dir.mkdir()
    (plugin_dir / "setup.sh").write_text(
        (_LSP_DIR / "setup.sh").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_node_home = tmp_path / "node"
    log_path = tmp_path / "setup.log"
    fake_path = f"{fake_bin}:/usr/bin:/bin"
    (fake_bin / "uname").write_text(
        "#!/usr/bin/env bash\nprintf 'x86_64\\n'\n",
        encoding="utf-8",
    )
    (fake_bin / "uname").chmod(0o755)
    (fake_bin / "curl").write_text(
        """#!/usr/bin/env bash
set -eu
printf 'curl %s\n' "$*" >> "$PYRIGHT_SETUP_LOG"
url=""
output=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        -o)
            output="$2"
            shift 2
            ;;
        http*)
            url="$1"
            shift
            ;;
        *)
            shift
            ;;
    esac
done
if [ "$url" = "https://first.invalid/node.tar.xz" ]; then
    exit 35
fi
printf 'fake archive\n' > "$output"
exit 0
""",
        encoding="utf-8",
    )
    (fake_bin / "curl").chmod(0o755)
    (fake_bin / "tar").write_text(
        """#!/usr/bin/env bash
set -eu
printf 'tar %s\n' "$*" >> "$PYRIGHT_SETUP_LOG"
mkdir -p "$EOS_NODE_HOME/bin"
cat > "$EOS_NODE_HOME/bin/node" <<'NODE'
#!/usr/bin/env bash
printf 'v22.13.1\n'
NODE
cat > "$EOS_NODE_HOME/bin/npm" <<'NPM'
#!/usr/bin/env bash
set -eu
printf 'npm %s\n' "$*" >> "$PYRIGHT_SETUP_LOG"
if [ "${1:-}" = "-v" ]; then
    printf '10.9.2\n'
    exit 0
fi
if [ "${1:-}" = "config" ] && [ "${2:-}" = "set" ]; then
    exit 0
fi
if [ "${1:-}" = "install" ]; then
    mkdir -p "$EOS_NODE_HOME/bin"
    printf '#!/usr/bin/env sh\nprintf "pyright 1.1.409\\n"\n' > "$EOS_NODE_HOME/bin/pyright"
    printf '#!/usr/bin/env sh\nexit 0\n' > "$EOS_NODE_HOME/bin/pyright-langserver"
    chmod +x "$EOS_NODE_HOME/bin/pyright" "$EOS_NODE_HOME/bin/pyright-langserver"
    exit 0
fi
exit 99
NPM
chmod +x "$EOS_NODE_HOME/bin/node" "$EOS_NODE_HOME/bin/npm"
""",
        encoding="utf-8",
    )
    (fake_bin / "tar").chmod(0o755)

    env = {
        "PATH": fake_path,
        "EOS_NODE_HOME": str(fake_node_home),
        "EOS_LSP_ALLOW_DOWNLOAD": "1",
        "EOS_NODE_DOWNLOAD_URLS": (
            "https://first.invalid/node.tar.xz "
            "https://second.invalid/node.tar.xz"
        ),
        "PYRIGHT_SETUP_LOG": str(log_path),
    }
    completed = subprocess.run(
        ["bash", str(plugin_dir / "setup.sh")],
        cwd=plugin_dir,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert (plugin_dir / ".pyright_installed").is_file()
    calls = log_path.read_text(encoding="utf-8").splitlines()
    assert any("https://first.invalid/node.tar.xz" in call for call in calls)
    assert any("https://second.invalid/node.tar.xz" in call for call in calls)
    assert any(call.startswith("tar ") for call in calls)
