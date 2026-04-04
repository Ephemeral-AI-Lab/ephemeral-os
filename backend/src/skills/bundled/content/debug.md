# debug

Diagnose and fix bugs systematically.

## When to use

Use when the user reports a bug, error, or unexpected behavior.

## Workflow

1. Reproduce: understand the exact steps that trigger the issue
2. Read the error: stack traces, log messages, error codes
3. Locate: use Grep/Read to find the relevant code path
4. Hypothesize: form a theory about the root cause
5. Verify: add logging or read surrounding code to confirm
6. Fix: make the minimal change that addresses the root cause
7. Test: verify the fix works and doesn't break other things

## Rules

- Read the error message carefully before searching code
- Don't guess — verify your hypothesis before changing code
- Fix the root cause, not the symptom
- Don't retry the same approach if it failed — investigate why
- If stuck after 3 attempts, explain what you've tried and ask for help
