#!/usr/bin/env python3
"""Validate local HTML wiki pages for navigation and rendering hazards."""

from __future__ import annotations

import argparse
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urldefrag


VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stack: list[tuple[str, tuple[int, int]]] = []
        self.ids: list[str] = []
        self.hrefs: list[str] = []
        self.stylesheets: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: value or "" for key, value in attrs}
        if "id" in attr:
            self.ids.append(attr["id"])
        if tag == "a" and "href" in attr:
            self.hrefs.append(attr["href"])
        if tag == "link" and attr.get("rel") == "stylesheet" and "href" in attr:
            self.stylesheets.append(attr["href"])
        if tag not in VOID_TAGS:
            self.stack.append((tag, self.getpos()))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS and self.stack:
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID_TAGS:
            return
        if not self.stack:
            self.errors.append(f"unexpected </{tag}> at {self.getpos()}")
            return
        open_tag, pos = self.stack.pop()
        if open_tag != tag:
            self.errors.append(
                f"mismatched </{tag}> at {self.getpos()}, expected </{open_tag}> from {pos}"
            )


def is_external(href: str) -> bool:
    return bool(re.match(r"^(https?:|mailto:|tel:|javascript:)", href))


def parse_page(path: Path) -> PageParser:
    parser = PageParser()
    parser.feed(path.read_text(encoding="utf-8"))
    if parser.stack:
        tail = ", ".join(f"<{tag}> at {pos}" for tag, pos in parser.stack[-5:])
        parser.errors.append(f"unclosed tags: {tail}")
    return parser


def check_css(path: Path) -> list[str]:
    if not path.exists():
        return [f"missing stylesheet: {path}"]
    depth = 0
    for index, char in enumerate(path.read_text(encoding="utf-8")):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        if depth < 0:
            return [f"extra closing brace in {path} at byte {index}"]
    if depth:
        return [f"unbalanced braces in {path}: depth {depth}"]
    return []


def check_page(path: Path) -> dict[str, object]:
    html = path.read_text(encoding="utf-8")
    parser = parse_page(path)
    errors = list(parser.errors)
    warnings: list[str] = []

    if re.search(r"\[\[|\]\]", html):
        errors.append("rendered Obsidian-style [[...]] syntax remains")

    seen: set[str] = set()
    duplicate_ids = sorted({id_ for id_ in parser.ids if id_ in seen or seen.add(id_)})
    if duplicate_ids:
        errors.append(f"duplicate ids: {duplicate_ids}")

    current_ids = set(parser.ids)
    parsed_targets: dict[Path, set[str]] = {path.resolve(): current_ids}

    for href in parser.hrefs:
        if is_external(href):
            continue
        target, fragment = urldefrag(href)
        target_path = (path.parent / target).resolve() if target else path.resolve()
        if target and not target_path.exists():
            errors.append(f"missing link target: {href}")
            continue
        if fragment:
            if target_path not in parsed_targets:
                if target_path.suffix == ".html":
                    parsed_targets[target_path] = set(parse_page(target_path).ids)
                else:
                    parsed_targets[target_path] = set()
            if fragment not in parsed_targets[target_path]:
                errors.append(f"missing anchor: {href}")

    for stylesheet in parser.stylesheets:
        if is_external(stylesheet):
            continue
        css_target, _fragment = urldefrag(stylesheet)
        errors.extend(check_css((path.parent / css_target).resolve()))

    long_tokens = sorted(
        {
            token
            for token in re.split(r"\s+", re.sub(r"<[^>]+>", " ", html))
            if len(token) > 80
        }
    )
    if long_tokens:
        warnings.append(f"{len(long_tokens)} very long text tokens; verify wrapping CSS")

    return {
        "file": str(path),
        "ids": len(parser.ids),
        "hrefs": len(parser.hrefs),
        "stylesheets": len(parser.stylesheets),
        "errors": errors,
        "warnings": warnings,
    }


def main() -> int:
    arg_parser = argparse.ArgumentParser()
    arg_parser.add_argument("pages", nargs="+", help="HTML pages to validate")
    args = arg_parser.parse_args()

    results = [check_page(Path(page)) for page in args.pages]
    print(json.dumps(results, indent=2))
    return 1 if any(result["errors"] for result in results) else 0


if __name__ == "__main__":
    sys.exit(main())
