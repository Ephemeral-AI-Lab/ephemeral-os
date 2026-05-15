"""Parse the already-rendered SWE-EVO user input into planning hints."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from itertools import count

_PR_BLOCK_RE = re.compile(
    r"<pr_description>\s*(?P<body>.*?)\s*</pr_description>",
    re.DOTALL | re.IGNORECASE,
)
_BULLET_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)(?P<text>.+?)\s*$")
_PR_ID_RE = re.compile(r":pr:`(?P<id>\d+)`|#(?P<hash_id>\d+)")
_UNDERLINE_RE = re.compile(r"^[=\-^~`#*]{3,}$")

_KNOWN_HEADINGS = {
    "enhancements",
    "bug fixes",
    "deprecations",
    "documentation",
    "maintenance",
}

_SUBSYSTEM_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("parquet", ("parquet", "pyarrow", "arrow", "orc")),
    ("distributed", ("distributed", "scheduler", "worker", "cluster", "runtime")),
    ("dataframe", ("dataframe", "groupby", "series", "index", "dtype", "dtypes")),
    ("array", ("array", "chunk", "chunks", "xarray")),
    ("config", ("config", "configuration", "update_defaults", "defaults")),
    ("io", ("read_", "to_", "csv", "json", "hdf", "sql", "store", "filesystem")),
    ("cli", ("cli", "command", "list", "get", "argument", "option")),
    ("compat", ("compat", "deprecat", "numpy", "pandas", "python", "version")),
    ("docs", ("doc", "documentation", "example", "readme")),
    ("maintenance", ("maint", "pin", "dependency", "ci", "test", "chore")),
)

_HIGH_RISK_SUBSYSTEMS = {"parquet", "io", "distributed", "compat"}
_MEDIUM_RISK_SUBSYSTEMS = {"cli", "config"}


@dataclass(frozen=True, slots=True)
class RequirementItem:
    id: str
    heading: str
    text: str
    pr_id: str | None
    subsystem: str
    risk: str
    weight: int


@dataclass(frozen=True, slots=True)
class WorkPackage:
    id: str
    title: str
    subsystem: str
    item_ids: tuple[str, ...]
    weight: int
    risk: str
    deps: tuple[str, ...] = ()
    recursive_candidate: bool = False


@dataclass(frozen=True, slots=True)
class UserInputPlan:
    inspected_text: str
    requirements: tuple[RequirementItem, ...]
    packages: tuple[WorkPackage, ...]


def build_user_input_plan(user_prompt: str) -> UserInputPlan:
    inspected = inspected_user_input(user_prompt)
    requirements = parse_requirement_items(inspected)
    return UserInputPlan(
        inspected_text=inspected,
        requirements=requirements,
        packages=build_work_packages(requirements),
    )


def inspected_user_input(user_prompt: str) -> str:
    match = _PR_BLOCK_RE.search(user_prompt)
    if match is not None:
        return match.group("body")
    return user_prompt


def parse_requirement_items(text: str) -> tuple[RequirementItem, ...]:
    items: list[RequirementItem] = []
    heading = "General"
    pending_heading: str | None = None
    item_no = count(1)

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            pending_heading = None
            continue
        lower = stripped.lower()
        if _UNDERLINE_RE.match(stripped):
            if pending_heading:
                heading = pending_heading
            continue
        if lower in _KNOWN_HEADINGS:
            heading = stripped
            pending_heading = stripped
            continue
        if not _BULLET_RE.match(line) and 1 <= len(stripped) <= 80:
            pending_heading = stripped

        match = _BULLET_RE.match(line)
        if match is None:
            continue
        item_text = match.group("text").strip()
        subsystem = _classify_subsystem(item_text, heading)
        risk = _classify_risk(item_text, heading, subsystem)
        item = RequirementItem(
            id=f"req_{next(item_no):03d}",
            heading=heading,
            text=item_text,
            pr_id=_extract_pr_id(item_text),
            subsystem=subsystem,
            risk=risk,
            weight=_weight_for(item_text, risk),
        )
        items.append(item)

    return tuple(items)


def build_work_packages(
    requirements: tuple[RequirementItem, ...],
) -> tuple[WorkPackage, ...]:
    by_subsystem: dict[str, list[RequirementItem]] = defaultdict(list)
    for item in requirements:
        by_subsystem[item.subsystem].append(item)

    packages: list[WorkPackage] = []
    package_no = count(1)
    for subsystem in _ordered_subsystems(by_subsystem):
        chunk: list[RequirementItem] = []
        chunk_weight = 0
        high_risk_count = 0
        for item in by_subsystem[subsystem]:
            item_high = item.risk == "high"
            if chunk and (
                chunk_weight + item.weight > 12
                or (item_high and high_risk_count >= 6)
            ):
                packages.append(_make_package(next(package_no), subsystem, chunk))
                chunk = []
                chunk_weight = 0
                high_risk_count = 0
            chunk.append(item)
            chunk_weight += item.weight
            high_risk_count += int(item_high)
        if chunk:
            packages.append(_make_package(next(package_no), subsystem, chunk))

    recursive = _make_recursive_candidate(requirements, packages, next(package_no))
    if recursive is not None:
        packages.append(recursive)
    return tuple(packages)


def _ordered_subsystems(
    by_subsystem: dict[str, list[RequirementItem]],
) -> tuple[str, ...]:
    preferred = tuple(name for name, _ in _SUBSYSTEM_KEYWORDS) + ("unknown",)
    seen = set(by_subsystem)
    ordered = [name for name in preferred if name in seen]
    ordered.extend(sorted(seen - set(ordered)))
    return tuple(ordered)


def _make_package(
    seq: int,
    subsystem: str,
    items: list[RequirementItem],
) -> WorkPackage:
    risk = _max_risk(item.risk for item in items)
    return WorkPackage(
        id=f"pkg_{subsystem}_{seq:02d}",
        title=f"{subsystem.title()} requirements {seq:02d}",
        subsystem=subsystem,
        item_ids=tuple(item.id for item in items),
        weight=sum(item.weight for item in items),
        risk=risk,
        recursive_candidate=False,
    )


def _make_recursive_candidate(
    requirements: tuple[RequirementItem, ...],
    packages: list[WorkPackage],
    seq: int,
) -> WorkPackage | None:
    if len(requirements) < 40:
        return None
    selected: list[RequirementItem] = []
    wanted = ("config", "io", "parquet", "distributed", "compat")
    for subsystem in wanted:
        selected.extend(
            item for item in requirements if item.subsystem == subsystem
        )
        if len(selected) >= 18:
            break
    if not selected:
        selected = list(requirements[:18])
    deps = tuple(pkg.id for pkg in packages[: min(4, len(packages))])
    return WorkPackage(
        id=f"pkg_recursive_release_{seq:02d}",
        title="Cross-subsystem release integration",
        subsystem="integration",
        item_ids=tuple(item.id for item in selected[:24]),
        weight=sum(item.weight for item in selected[:24]),
        risk="high",
        deps=deps,
        recursive_candidate=True,
    )


def _extract_pr_id(text: str) -> str | None:
    match = _PR_ID_RE.search(text)
    if match is None:
        return None
    return match.group("id") or match.group("hash_id")


def _classify_subsystem(text: str, heading: str) -> str:
    lower = f"{heading} {text}".lower()
    for subsystem, keywords in _SUBSYSTEM_KEYWORDS:
        if any(keyword in lower for keyword in keywords):
            return subsystem
    return "unknown"


def _matching_subsystems(text: str, heading: str) -> set[str]:
    lower = f"{heading} {text}".lower()
    return {
        subsystem
        for subsystem, keywords in _SUBSYSTEM_KEYWORDS
        if any(keyword in lower for keyword in keywords)
    }


def _classify_risk(text: str, heading: str, subsystem: str) -> str:
    lower = f"{heading} {text}".lower()
    matching = _matching_subsystems(text, heading)
    if (
        len(matching) > 1
        or subsystem in _HIGH_RISK_SUBSYSTEMS
        or "deprecat" in lower
        or "compat" in lower
    ):
        return "high"
    if (
        subsystem in _MEDIUM_RISK_SUBSYSTEMS
        or "user" in lower
        or "behavior" in lower
    ):
        return "medium"
    return "low"


def _weight_for(text: str, risk: str) -> int:
    base = {"low": 1, "medium": 2, "high": 3}[risk]
    if len(text) > 180:
        base += 1
    if len(text) > 300:
        base += 1
    return base


def _max_risk(risks: object) -> str:
    order = {"low": 0, "medium": 1, "high": 2}
    return max(risks, key=lambda risk: order[str(risk)])  # type: ignore[arg-type]


__all__ = [
    "RequirementItem",
    "UserInputPlan",
    "WorkPackage",
    "build_user_input_plan",
    "build_work_packages",
    "inspected_user_input",
    "parse_requirement_items",
]
