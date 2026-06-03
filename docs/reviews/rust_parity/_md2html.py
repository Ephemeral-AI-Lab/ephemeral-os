#!/usr/bin/env python3
"""Dependency-free GFM->HTML for the rust-parity audit docs.

Handles the subset the audit uses: ATX headers, GFM tables (pipes safe inside
backtick spans), fenced code, blockquotes, nested -/* and ordered lists, hr,
inline code/bold/italic/links. Produces a self-contained styled HTML file.

Usage:
  _md2html.py FILE.md [FILE2.md ...]        # one .html next to each .md
  _md2html.py --index REPORT.md areas/*.md  # also build index.html linking all
"""
import sys
import re
import html
import os

CSS = """
:root{--fg:#1b1f24;--muted:#57606a;--bg:#ffffff;--soft:#f6f8fa;--border:#d0d7de;
--link:#0969da;--hi:#cf222e;--med:#9a6700;--low:#1a7f37;--code:#0550ae;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;}
.wrap{max-width:1080px;margin:0 auto;padding:32px 28px 96px;}
.crumb{font-size:12px;color:var(--muted);margin-bottom:18px;}
.crumb a{color:var(--muted);}
h1,h2,h3,h4{line-height:1.25;font-weight:650;margin:1.6em 0 .6em;}
h1{font-size:28px;border-bottom:1px solid var(--border);padding-bottom:.3em;margin-top:.2em;}
h2{font-size:22px;border-bottom:1px solid var(--border);padding-bottom:.25em;}
h3{font-size:18px;} h4{font-size:15px;color:var(--muted);}
a{color:var(--link);text-decoration:none;} a:hover{text-decoration:underline;}
p{margin:.6em 0;} ul,ol{margin:.5em 0;padding-left:1.6em;} li{margin:.18em 0;}
code{background:var(--soft);border:1px solid var(--border);border-radius:5px;
padding:.1em .35em;font:12.5px/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
color:var(--code);white-space:pre-wrap;word-break:break-word;}
pre{background:var(--soft);border:1px solid var(--border);border-radius:8px;
padding:14px 16px;overflow:auto;} pre code{background:none;border:0;padding:0;color:var(--fg);}
blockquote{margin:.8em 0;padding:.2em 1em;color:var(--muted);
border-left:3px solid var(--border);background:var(--soft);border-radius:0 6px 6px 0;}
hr{border:0;border-top:1px solid var(--border);margin:1.8em 0;}
table{border-collapse:collapse;width:100%;margin:1em 0;font-size:13.5px;display:block;overflow-x:auto;}
th,td{border:1px solid var(--border);padding:7px 10px;text-align:left;vertical-align:top;}
th{background:var(--soft);font-weight:650;}
tr:nth-child(even) td{background:#fbfcfd;}
td code,th code{font-size:12px;}
/* severity / status tinting */
td:first-child{white-space:nowrap;}
.sev-high,td.high{color:var(--hi);font-weight:600;}
.sev-med,td.med{color:var(--med);font-weight:600;}
.sev-low,td.low{color:var(--low);}
.footer{margin-top:64px;color:var(--muted);font-size:12px;border-top:1px solid var(--border);padding-top:14px;}
"""

_TOK = "\x00%d\x00"


def inline(s):
    toks = []

    def stash(m):
        toks.append(m.group(1))
        return _TOK % (len(toks) - 1)

    s = re.sub(r"`([^`]+)`", stash, s)
    s = html.escape(s, quote=False)
    s = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)",
               lambda m: '<a href="%s">%s</a>' % (html.escape(m.group(2), quote=True), m.group(1)), s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![\*\w])\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", s)

    def restore(m):
        return "<code>%s</code>" % html.escape(toks[int(m.group(1))], quote=False)

    return re.sub(r"\x00(\d+)\x00", restore, s)


def split_pipes(row):
    """Split a table row on | that are not inside backtick spans."""
    out, cur, in_code = [], [], False
    for ch in row:
        if ch == "`":
            in_code = not in_code
            cur.append(ch)
        elif ch == "|" and not in_code:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


def cells(row):
    r = row.strip()
    if r.startswith("|"):
        r = r[1:]
    if r.endswith("|"):
        r = r[:-1]
    return [c.strip() for c in split_pipes(r)]


def sev_class(text):
    t = text.strip().lower().strip("*` ")
    if t in ("high", "blocker", "critical"):
        return ' class="high"'
    if t in ("med", "medium"):
        return ' class="med"'
    if t in ("low", "none", "info"):
        return ' class="low"'
    return ""


def is_block_start(line):
    s = line.strip()
    return (s.startswith("#") or s.startswith("```") or s.startswith(">")
            or re.match(r"^([-*+]|\d+\.)\s+", s) or re.match(r"^(---|\*\*\*|___)\s*$", s)
            or "|" in line)


def parse_list(lines, i, n):
    """Parse a (possibly nested) list starting at lines[i]. Returns (html, new_i)."""
    def indent(l):
        return len(l) - len(l.lstrip(" "))

    base = indent(lines[i])
    ordered = bool(re.match(r"^\s*\d+\.\s+", lines[i]))
    items = []
    while i < n:
        l = lines[i]
        if not l.strip():
            # allow a single blank line inside a list if next is a deeper/same item
            if i + 1 < n and re.match(r"^\s*([-*+]|\d+\.)\s+", lines[i + 1]) and indent(lines[i + 1]) >= base:
                i += 1
                continue
            break
        m = re.match(r"^(\s*)([-*+]|\d+\.)\s+(.*)$", l)
        if not m or indent(l) < base:
            break
        if indent(l) > base:
            # nested list belongs to previous item
            sub, i = parse_list(lines, i, n)
            if items:
                items[-1] += sub
            continue
        items.append("<li>%s</li>" % inline(m.group(3)))
        i += 1
        # continuation lines (indented, not a new bullet)
        while i < n and lines[i].strip() and indent(lines[i]) > base and not re.match(r"^\s*([-*+]|\d+\.)\s+", lines[i]):
            items[-1] = items[-1][:-5] + " " + inline(lines[i].strip()) + "</li>"
            i += 1
    tag = "ol" if ordered else "ul"
    return "<%s>%s</%s>" % (tag, "".join(items), tag), i


def convert_body(md):
    lines = md.split("\n")
    n = len(lines)
    out = []
    i = 0
    while i < n:
        line = lines[i]
        s = line.strip()
        if s.startswith("```"):
            i += 1
            buf = []
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            out.append("<pre><code>%s</code></pre>" % html.escape("\n".join(buf), quote=False))
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            lvl = len(m.group(1))
            out.append("<h%d>%s</h%d>" % (lvl, inline(m.group(2).strip()), lvl))
            i += 1
            continue
        if "|" in line and i + 1 < n and re.match(r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$", lines[i + 1]) and "|" in lines[i + 1]:
            header = cells(line)
            i += 2
            body = []
            while i < n and "|" in lines[i] and lines[i].strip():
                body.append(cells(lines[i]))
                i += 1
            th = "".join("<th>%s</th>" % inline(c) for c in header)
            trs = []
            for row in body:
                tds = "".join("<td%s>%s</td>" % (sev_class(c), inline(c)) for c in row)
                trs.append("<tr>%s</tr>" % tds)
            out.append("<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>" % (th, "".join(trs)))
            continue
        if s.startswith(">"):
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            out.append("<blockquote>%s</blockquote>" % inline(" ".join(buf)))
            continue
        if re.match(r"^\s*([-*+]|\d+\.)\s+", line):
            block, i = parse_list(lines, i, n)
            out.append(block)
            continue
        if re.match(r"^(---|\*\*\*|___)\s*$", s):
            out.append("<hr>")
            i += 1
            continue
        if not s:
            i += 1
            continue
        buf = [line]
        i += 1
        while i < n and lines[i].strip() and not is_block_start(lines[i]):
            buf.append(lines[i])
            i += 1
        out.append("<p>%s</p>" % inline(" ".join(b.strip() for b in buf)))
    return "\n".join(out)


def page(title, body, crumb=""):
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>%s</title><style>%s</style></head><body><div class=\"wrap\">%s%s"
            "<div class=\"footer\">Rust↔Python parity audit · generated from %s</div>"
            "</div></body></html>") % (html.escape(title), CSS, crumb, body, html.escape(title))


def title_of(md, fallback):
    m = re.search(r"^#\s+(.*)$", md, re.M)
    return m.group(1).strip() if m else fallback


def main(argv):
    make_index = False
    if argv and argv[0] == "--index":
        make_index = True
        argv = argv[1:]
    files = argv
    converted = []
    for f in files:
        md = open(f, encoding="utf-8").read()
        title = title_of(md, os.path.basename(f))
        crumb = '<div class="crumb"><a href="./REPORT.html">parity report</a> / %s</div>' % html.escape(os.path.basename(f)) if "areas/" in f or os.path.basename(os.path.dirname(f)) == "areas" else ""
        out_path = f[:-3] + ".html" if f.endswith(".md") else f + ".html"
        open(out_path, "w", encoding="utf-8").write(page(title, convert_body(md), crumb))
        converted.append((out_path, title))
        print("wrote", out_path)
    if make_index and converted:
        items = "".join('<li><a href="%s">%s</a></li>' % (os.path.relpath(p, os.path.dirname(converted[0][0])), html.escape(t)) for p, t in converted)
        idx = page("Rust parity audit — index", "<h1>Rust ↔ Python parity audit</h1><ul>%s</ul>" % items)
        ipath = os.path.join(os.path.dirname(converted[0][0]), "index.html")
        open(ipath, "w", encoding="utf-8").write(idx)
        print("wrote", ipath)


if __name__ == "__main__":
    main(sys.argv[1:])
