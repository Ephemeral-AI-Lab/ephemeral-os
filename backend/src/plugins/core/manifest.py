"""Parse and validate ``plugin.md`` frontmatter.

The manifest is the single source of truth for a plugin's exported tools,
optional setup script, and optional in-sandbox runtime. See
``docs/architecture/plugins-refactor.md`` §1.1 for the contract.

Path resolution is strict: every declared path must resolve under the plugin
directory (no ``..`` escape) and exist on disk. Tool *binding* (importing the
module and verifying it exposes a ``BaseTool``) is the loader's job
(:mod:`plugins.core.loader`); this module only validates structure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "ALLOWED_PLUGIN_KINDS",
    "PluginManifest",
    "PluginManifestError",
    "ToolEntry",
    "parse_plugin_manifest",
]


class PluginManifestError(ValueError):
    """Raised when a ``plugin.md`` fails schema validation."""


_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(?P<frontmatter>.*?)\n---\s*(?:\n(?P<body>.*))?\Z",
    re.DOTALL,
)

# V3 README §Requirement 2 enum — values for ``plugin_kind`` in the audit
# payload. Closer D (Phase 2.6): manifest authors may declare ``kind`` so
# the plugin shim can stamp the real value on ``plugin.*`` events instead of
# defaulting to ``"custom"``.
ALLOWED_PLUGIN_KINDS: frozenset[str] = frozenset(
    {
        "language_server",
        "formatter",
        "indexer",
        "build_daemon",
        "mcp_bridge",
        "custom",
    }
)


@dataclass(frozen=True)
class ToolEntry:
    """One declared tool in a manifest."""

    name: str
    module: Path  # absolute path under the plugin directory


@dataclass(frozen=True)
class PluginManifest:
    """Parsed and validated ``plugin.md``."""

    name: str
    description: str
    tools: tuple[ToolEntry, ...]
    setup: Path | None  # absolute path or None
    runtime: Path | None  # absolute path or None
    source_dir: Path  # absolute path to the plugin directory
    body: str  # markdown body after the frontmatter (informational)
    kind: str | None = None  # one of ALLOWED_PLUGIN_KINDS, or None for unset


def parse_plugin_manifest(plugin_dir: Path) -> PluginManifest:
    """Parse ``<plugin_dir>/plugin.md`` and validate the schema."""
    plugin_dir = plugin_dir.resolve()
    manifest_path = plugin_dir / "plugin.md"
    if not manifest_path.is_file():
        raise PluginManifestError(f"plugin.md missing under {plugin_dir}")

    text = manifest_path.read_text(encoding="utf-8")
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        raise PluginManifestError(
            "plugin.md must begin with a `---`-delimited YAML frontmatter "
            f"block: {manifest_path}"
        )

    try:
        data = yaml.safe_load(match.group("frontmatter")) or {}
    except yaml.YAMLError as exc:
        raise PluginManifestError(
            f"plugin.md frontmatter is not valid YAML: {manifest_path}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise PluginManifestError(
            f"plugin.md frontmatter must be a YAML mapping: {manifest_path}"
        )

    name = _require_str(data, "name", manifest_path)
    if not _NAME_PATTERN.match(name):
        raise PluginManifestError(
            f"plugin.md name {name!r} must match {_NAME_PATTERN.pattern}: "
            f"{manifest_path}"
        )
    if name != plugin_dir.name:
        raise PluginManifestError(
            f"plugin.md name {name!r} does not match directory name "
            f"{plugin_dir.name!r}: {manifest_path}"
        )

    description = _require_str(data, "description", manifest_path)
    tools = _parse_tools(data, plugin_dir, manifest_path, plugin_name=name)
    setup = _resolve_setup(data, plugin_dir, manifest_path)
    runtime = _resolve_optional_path(
        data, "runtime", plugin_dir, manifest_path
    )
    kind = _parse_kind(data, manifest_path)

    body_match = match.group("body")
    body = (body_match or "").strip()

    return PluginManifest(
        name=name,
        description=description,
        tools=tuple(tools),
        setup=setup,
        runtime=runtime,
        source_dir=plugin_dir,
        body=body,
        kind=kind,
    )


def _parse_kind(
    data: dict[str, Any],
    manifest_path: Path,
) -> str | None:
    """Validate the optional ``kind`` field against the V3 enum.

    Closer D (Phase 2.6): authors declare one of
    :data:`ALLOWED_PLUGIN_KINDS` so the audit shim stamps the right value
    on ``plugin.*`` events. Unknown values are a hard error — silent typos
    would broaden the schema invisibly.
    """
    raw = data.get("kind")
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise PluginManifestError(
            f"plugin.md kind must be a non-empty string when set: {manifest_path}"
        )
    value = raw.strip()
    if value not in ALLOWED_PLUGIN_KINDS:
        allowed = ", ".join(sorted(ALLOWED_PLUGIN_KINDS))
        raise PluginManifestError(
            f"plugin.md kind {value!r} is not one of [{allowed}]: {manifest_path}"
        )
    return value


def _parse_tools(
    data: dict[str, Any],
    plugin_dir: Path,
    manifest_path: Path,
    *,
    plugin_name: str,
) -> list[ToolEntry]:
    tools_raw = data.get("tools")
    if not isinstance(tools_raw, list) or not tools_raw:
        raise PluginManifestError(
            f"plugin.md tools must be a non-empty list: {manifest_path}"
        )
    expected_prefix = f"{plugin_name}."
    seen_names: set[str] = set()
    tools: list[ToolEntry] = []
    for index, entry in enumerate(tools_raw):
        if not isinstance(entry, dict):
            raise PluginManifestError(
                f"plugin.md tools[{index}] must be a mapping: {manifest_path}"
            )
        tool_name = _require_str(
            entry, "name", manifest_path, path=f"tools[{index}].name"
        )
        if not tool_name.startswith(expected_prefix):
            raise PluginManifestError(
                f"plugin.md tools[{index}].name {tool_name!r} must start "
                f"with {expected_prefix!r}: {manifest_path}"
            )
        if tool_name in seen_names:
            raise PluginManifestError(
                f"plugin.md duplicate tool name {tool_name!r}: {manifest_path}"
            )
        seen_names.add(tool_name)
        module_raw = _require_str(
            entry, "module", manifest_path, path=f"tools[{index}].module"
        )
        module_path = _resolve_under(
            plugin_dir,
            module_raw,
            manifest_path,
            field=f"tools[{index}].module",
        )
        if not module_path.is_file():
            raise PluginManifestError(
                f"plugin.md tools[{index}].module path does not exist: "
                f"{module_path}"
            )
        tools.append(ToolEntry(name=tool_name, module=module_path))
    return tools


def _resolve_setup(
    data: dict[str, Any],
    plugin_dir: Path,
    manifest_path: Path,
) -> Path | None:
    raw = data.get("setup")
    if raw is None:
        default = plugin_dir / "setup.sh"
        return default if default.is_file() else None
    if not isinstance(raw, str) or not raw.strip():
        raise PluginManifestError(
            "plugin.md setup must be a non-empty string when set: "
            f"{manifest_path}"
        )
    setup_path = _resolve_under(
        plugin_dir, raw.strip(), manifest_path, field="setup"
    )
    if not setup_path.is_file():
        raise PluginManifestError(
            f"plugin.md setup path does not exist: {setup_path}"
        )
    return setup_path


def _resolve_optional_path(
    data: dict[str, Any],
    field: str,
    plugin_dir: Path,
    manifest_path: Path,
) -> Path | None:
    raw = data.get(field)
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise PluginManifestError(
            f"plugin.md {field} must be a non-empty string when set: "
            f"{manifest_path}"
        )
    candidate = _resolve_under(
        plugin_dir, raw.strip(), manifest_path, field=field
    )
    if not candidate.is_file():
        raise PluginManifestError(
            f"plugin.md {field} path does not exist: {candidate}"
        )
    return candidate


def _require_str(
    data: dict[str, Any],
    field: str,
    manifest_path: Path,
    *,
    path: str | None = None,
) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        loc = path or field
        raise PluginManifestError(
            f"plugin.md {loc} must be a non-empty string: {manifest_path}"
        )
    return value.strip()


def _resolve_under(
    plugin_dir: Path,
    raw: str,
    manifest_path: Path,
    *,
    field: str,
) -> Path:
    candidate = (plugin_dir / raw).resolve()
    try:
        candidate.relative_to(plugin_dir)
    except ValueError as exc:
        raise PluginManifestError(
            f"plugin.md {field} path escapes plugin dir: {raw!r}: "
            f"{manifest_path}"
        ) from exc
    return candidate
