# UI Optimization Reference For Codebase Wikis

Use this reference when publishing or polishing HTML/Markdown codebase wikis.
The goal is not decoration; it is making dense source-backed architecture pages
readable, navigable, and consistent across sibling modules.

## Source Layer Boundary

- Treat CSS, navigation scripts, search indexes, templates, and shared HTML
  components as support-layer files.
- Keep source-truth claims in wiki pages grounded in code/tests/artifacts; UI
  polish must not change runtime code or alter technical claims.
- For multi-module HTML wikis, use one neutral root, one shared `assets/`
  directory, and one visual system for all module folders.

## Shared Design System

Use semantic tokens in the shared stylesheet:

- `--bg`, `--surface`, `--surface-soft`, `--text`, `--text-muted`
- `--border`, `--border-strong`
- `--accent`, `--accent-soft`, `--accent-border`
- `--info`, `--success`, `--warn`, `--danger` and matching soft backgrounds
- code, radius, shadow, content-width, and sidebar-width tokens

Avoid page-local raw colors unless the value becomes a reusable token. Keep the
dominant palette neutral with restrained blue accents, plus semantic status
colors for information, success, warning, and danger states.

## Readability Rules

- Body text should start at 16px with line-height around 1.55-1.7.
- Keep prose measure around 65-78 characters where possible.
- Do not use negative letter spacing; keep letter spacing at 0.
- Prefer readable cards, sequences, diagrams, and edge lists over dense tables.
- Use tables only for compact comparisons. Wrap long evidence paths and place
  long tables inside a horizontal `table-wrap`.
- Every flexible grid/flex child should have `min-width: 0`.
- Long paths, symbols, URLs, test names, and code spans must use
  `overflow-wrap: anywhere`.
- Reuse shared components: `page-toc`, `doc-actions`, `summary`, `callout`,
  `diagram`, `flow-row`, `node`, `sequence`, `edge-list`, `trace-grid`,
  `inline-kv`, `evidence-grid`, `page-baseline`, and card grids.

## Accessibility Rules

- Maintain at least WCAG AA contrast for text and important UI glyphs.
- Preserve visible `:focus-visible` states for links, inputs, summary elements,
  and any interactive control.
- Use semantic HTML landmarks and controls before adding visual wrappers.
- Do not rely on color alone: pair status color with labels, headings, or text.
- Keep search inputs and navigation targets large enough for comfortable use.
- Do not hide the local page TOC or page baseline behind hover-only behavior.

## Multi-Module Consistency

- Sandbox, tools, engine, TaskCenter, and future module pages must point to the
  same root shared stylesheet when they belong to one architecture wiki.
- Do not add module-specific CSS files for cosmetic differences.
- If one module has workflow diagrams, sibling module pages should use the same
  shared diagram/card components for comparable workflow depth.
- Inline `.wiki-link` links should read as text links in prose. Use `wiki-links`
  for grouped related-page shortcuts.
- Keep local `page-toc` shape identical across modules and place it immediately
  under the page title.

## Validation Checklist

Run the normal wiki validation, then inspect the changed pages for UI hazards:

```bash
python3 .agents/skills/codebase-wiki-builder/scripts/check_html_wiki.py <changed-html-pages>
git diff --check -- <changed-html-css-js-files>
rg -n "style=|<style|#[0-9a-fA-F]{3,8}" <changed-html-pages>
```

For broad shared-CSS edits, open at least one page from each affected module and
check:

- sidebar, module navigation, search input, local TOC, body text, tables,
  diagrams, callouts, evidence cards, and page baseline all use the same visual
  language;
- no horizontal page scroll at narrow widths;
- long file references wrap inside their containers;
- focus rings are visible;
- page content remains readable without relying on hover.
