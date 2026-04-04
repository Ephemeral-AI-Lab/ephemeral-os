# EphemeralOS Showcase

This page collects concrete ways to use EphemeralOS without overselling the project. Each example is intended to be small, reproducible, and easy to extend.

## 1. Repository-aware coding assistant

Use EphemeralOS as a lightweight local coding agent for reading code, making edits, and running validation commands.

```bash
uv run oh
```

Example prompt:

```text
Review this repo, identify the highest-risk bug, patch it, and run the relevant tests.
```

## 2. Headless automation for scripts and CI

The print mode is useful when you want structured output in shell pipelines or automation jobs.

```bash
uv run oh -p "Summarize the purpose of this repository" --output-format json
uv run oh -p "List files that define the permission system" --output-format stream-json
```

## 3. Skill and plugin playground

EphemeralOS can load Markdown skills and Claude-style plugin layouts, which makes it useful for experimentation with custom workflows.

Examples:

- Put a custom skill in `~/.ephemeralos/skills/`.
- Install a plugin into `~/.ephemeralos/plugins/`.
- Use the same workflow conventions across multiple local projects.

## 4. Multi-agent and background task experiments

The repo includes team coordination primitives, background task management, and task inspection tools.

Example prompts:

```text
Spawn a worker to audit the test suite while you inspect the CLI command registry.
```

```text
Create a background task that runs the slow integration script and report back when it finishes.
```

## 5. Provider compatibility testbed

EphemeralOS is useful when you need to compare Anthropic-compatible backends behind one harness.

Typical scenarios:

- Default Anthropic setup.
- Moonshot/Kimi through an Anthropic-compatible endpoint.
- Vertex-compatible and Bedrock-compatible gateways.
- Internal proxies that expose an Anthropic-style API surface.

See the provider compatibility table in [`README.md`](../README.md#-provider-compatibility).

## 6. Documentation-first onboarding

If you are evaluating the project rather than contributing code, start here:

- [`README.md`](../README.md) for install, usage, and architecture.
- [`CONTRIBUTING.md`](../CONTRIBUTING.md) for contributor workflow.
- [`CHANGELOG.md`](../CHANGELOG.md) for visible repo changes.

## How to contribute a showcase entry

Good showcase additions are:

- Based on a real workflow you ran.
- Short enough to reproduce locally.
- Honest about prerequisites and limitations.
- Focused on what EphemeralOS makes easier, not on generic LLM claims.
