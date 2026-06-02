Terminate the advisor with your verdict + summary.

Call this exactly once when:
- You've reviewed the caller's pending terminal submission against
  their contract and transcript.

Inputs:
- `verdict`: "approve" or "reject".
- `summary`: focused prose covering, in order:
  1. Tool selection — "correct" or "should be <other_tool>" with a
     one-sentence rationale.
  2. Quality of the work backing the payload — what's solid, what's
     unsupported. Quote transcript lines or contract fragments.
  3. Residual risks (or "None") — what the caller should weigh on
     approve, or the single most important fix on reject.

Behavior:
- The verdict + summary is returned to the caller via `ask_advisor`.
  The caller decides whether to proceed with their submission or
  revise.