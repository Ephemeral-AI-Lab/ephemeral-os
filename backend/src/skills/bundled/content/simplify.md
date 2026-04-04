# simplify

Refactor code to be simpler and more maintainable.

## When to use

Use when the user asks to simplify, clean up, or refactor code.

## Workflow

1. Read the target code completely
2. Identify complexity sources:
   - Unnecessary abstractions or indirection
   - Duplicated logic that should be consolidated
   - Over-engineered patterns (factories, builders, strategies for simple cases)
   - Dead code paths or unused variables
3. Propose changes that reduce line count while preserving behavior
4. Verify the refactored code passes existing tests

## Rules

- Don't add features or change behavior — simplification only
- Prefer deleting code over refactoring it
- Three similar lines are better than a premature abstraction
- Remove backwards-compatibility shims for removed features
- Don't add comments to explain simple code
