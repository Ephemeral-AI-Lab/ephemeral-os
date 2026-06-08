---
intent: read_only
terminal: true
hooks: []
---
Terminate as a subagent with your read-only findings.

Call this when:
- You've completed the investigation you were spawned to do.
- You can present your findings with verifiable references (paths,
  line numbers, command outputs).

Inputs:
- `summary`: 1–3 sentence recap answering your original question
  directly.
- `findings`: bullet list of concrete observations.
- `references`: list of citable evidence (e.g.,
  `path/to/file.py:42`, `git log` excerpts) that backs each finding.

Behavior:
- Your summary is returned to the caller via `run_subagent`'s result.

Style:
- You are read-only. Do not propose changes; describe what is.
- Cite evidence. A finding without a reference is just an assertion.
