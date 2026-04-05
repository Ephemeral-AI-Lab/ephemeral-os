---
name: e2e-test-skill
description: Expert guidance for E2E testing tasks with explicit verifiable instructions
---

# E2E Test Skill

## Purpose
This skill provides structured guidance for testing agent tool call accuracy and task completion.

## When to Use
Use when the user asks you to:
- Verify tool call accuracy
- Test if an agent follows instructions correctly
- Complete a multi-step testing task

## Instructions

### Critical Rule: EXACT STRING MATCHING
When verifying test results, you MUST use EXACT string matching. The verification string must appear verbatim in the output.

### Step-by-Step Verification Workflow

1. **Identify the expected tool** - Determine which tool is needed for each step
2. **Execute the tool** - Call the tool with the exact parameters required
3. **Verify with EXACT MATCH** - Check that output contains the exact expected string
4. **Report findings** - Use the format specified below

### Verification Output Format

When completing a verification task, you MUST include:
- `TOOL_CALLED: <exact_tool_name>` - The exact tool name used
- `PARAMS_USED: <json>` - The exact parameters passed
- `VERIFIED: <exact_expected_string>` - The exact string found in output
- `STATUS: PASS|FAIL` - Whether verification succeeded

### Example

User asks: "Verify tool X was called with param Y and output contains 'SUCCESS'"

Correct response:
```
TOOL_CALLED: daytona_bash
PARAMS_USED: {"command": "echo SUCCESS"}
VERIFIED: SUCCESS
STATUS: PASS
```

Incorrect response (vague/non-verifiable):
```
The tool was called and it worked. SUCCESS was in the output.
STATUS: PASS
```

### Multi-Step Task Completion

For multi-step tasks (5+ steps), you MUST:
1. Complete EACH step sequentially
2. Report progress after each step
3. Never skip steps or summarize early
4. Final report must list ALL completed steps

### Skill Loading Verification

When asked to verify "skill was loaded correctly", you MUST:
1. Call `load_skill` with the skill name
2. Verify the response contains expected instruction sections
3. Confirm the skill content matches what was expected
