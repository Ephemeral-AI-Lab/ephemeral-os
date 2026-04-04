# commit

Create clean, well-structured git commits.

## When to use

Use when the user asks to commit changes, create a PR, or prepare code for review.

## Workflow

1. Run `git status` and `git diff` to understand all changes
2. Analyze changes: categorize as feature, fix, refactor, docs, test, etc.
3. Draft a concise commit message:
   - First line: imperative mood, under 72 chars, describes the "why"
   - Body (if needed): explain context, trade-offs, or breaking changes
4. Stage only relevant files — never stage .env, credentials, or large binaries
5. Create the commit

## Rules

- Prefer specific `git add <file>` over `git add -A`
- Never use `--no-verify` unless explicitly asked
- Never amend published commits unless explicitly asked
- If a pre-commit hook fails, fix the issue and create a NEW commit (don't --amend)
- Include `Co-Authored-By` if pair programming
