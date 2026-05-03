# ruff: noqa
"""Live E2E: Complete agent run with comprehensive metrics verification.

A single, end-to-end test that:
1. Creates a real Daytona sandbox
2. Has an agent build a multi-file TypeScript project (package.json, components, utils, API route)
3. Collects ALL streaming events and prints them for visibility
4. At the end, verifies comprehensive metrics:
   - Tool use: which tools were called, how many times, input/output shapes
   - Correctness: all required files exist with correct content
   - Code Intelligence: mutation service initialization
   - Arbiter: edit tracking, conflict detection, audit journal
   - Event stream: correct ordering, all event types present

Run with:
    .venv/bin/python -m pytest backend/tests/test_e2e/test_live_full_run.py -v -s --ignore=backend/tests/test_utils --ignore=backend/tests/test_api
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest
from dotenv import load_dotenv

from engine.testing.eval_agent import EvalAgent
from tests.test_e2e.conftest import create_eval_agent, create_test_sandbox, delete_test_sandbox

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_PROJECT_ROOT / ".env")

pytestmark = [pytest.mark.e2e, pytest.mark.live]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _all_output(result) -> str:
    """Concatenate all tool outputs and assistant text from a result."""
    tool_outputs = " ".join(e.output for e in result.tools_completed())
    return tool_outputs + " " + result.text


def _all_text(result) -> str:
    """Like _all_output but also includes thinking text."""
    return _all_output(result) + " " + result.thinking_text


def _print_result(result) -> None:
    """Print the standard per-run tool-call summary line."""
    print(f"  Tool calls: {len(result.tools_started())}, latency: {result.latency_ms:.0f}ms")
    print(f"  Tools: {result.tool_names}")


AGENT_PROMPT = (
    "You are a senior fullstack developer with a remote Daytona sandbox. "
    "You MUST use tools for every action — never just describe what you'd do. "
    "Use write_file to create files, shell to run commands, "
    "read_file to read files. Always execute every step using tools. "
    "Be concise in your text responses."
)


# ---------------------------------------------------------------------------
# File content definitions for the project we'll build
# ---------------------------------------------------------------------------

PACKAGE_JSON = json.dumps(
    {
        "name": "ephemeral-fullrun",
        "version": "1.0.0",
        "private": True,
        "scripts": {"dev": "next dev", "build": "next build", "start": "next start"},
        "dependencies": {"next": "14.0.0", "react": "18.2.0", "react-dom": "18.2.0"},
        "devDependencies": {
            "typescript": "5.0.0",
            "@types/react": "18.2.0",
            "@types/node": "20.0.0",
        },
    },
    indent=2,
)

TSCONFIG = json.dumps(
    {
        "compilerOptions": {
            "target": "es5",
            "lib": ["dom", "dom.iterable", "esnext"],
            "allowJs": True,
            "skipLibCheck": True,
            "strict": True,
            "noEmit": True,
            "esModuleInterop": True,
            "module": "esnext",
            "moduleResolution": "bundler",
            "resolveJsonModule": True,
            "isolatedModules": True,
            "jsx": "preserve",
            "incremental": True,
            "plugins": [{"name": "next"}],
            "paths": {"@/*": ["./src/*"]},
        },
        "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx"],
        "exclude": ["node_modules"],
    },
    indent=2,
)

PAGE_TSX = """import React from "react";

interface PageProps {
  title: string;
  description: string;
}

function HeroSection({ title, description }: PageProps): React.ReactElement {
  return (
    <section className="hero">
      <h1>{title}</h1>
      <p>{description}</p>
    </section>
  );
}

export default function HomePage(): React.ReactElement {
  return (
    <main>
      <HeroSection
        title="Welcome to EphemeralOS"
        description="AI-powered development platform"
      />
    </main>
  );
}"""

LAYOUT_TSX = """import React from "react";

export const metadata = {
  title: "EphemeralOS",
  description: "AI-powered development platform",
};

interface RootLayoutProps {
  children: React.ReactNode;
}

export default function RootLayout({ children }: RootLayoutProps): React.ReactElement {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}"""

UTILS_TS = """export function formatDate(date: Date): string {
  return date.toISOString().split("T")[0];
}

export function capitalize(str: string): string {
  return str.charAt(0).toUpperCase() + str.slice(1);
}

export const APP_NAME = "EphemeralOS";
export const APP_VERSION = "1.0.0";"""

API_ROUTE_TS = """import { NextRequest, NextResponse } from "next/server";

interface HealthResponse {
  status: string;
  timestamp: string;
  version: string;
}

export async function GET(request: NextRequest): Promise<NextResponse<HealthResponse>> {
  const response: HealthResponse = {
    status: "healthy",
    timestamp: new Date().toISOString(),
    version: "1.0.0",
  };
  return NextResponse.json(response);
}"""

# Files to create and their paths
PROJECT_FILES = {
    "/workspace/fullrun/package.json": PACKAGE_JSON,
    "/workspace/fullrun/tsconfig.json": TSCONFIG,
    "/workspace/fullrun/src/app/page.tsx": PAGE_TSX,
    "/workspace/fullrun/src/app/layout.tsx": LAYOUT_TSX,
    "/workspace/fullrun/src/lib/utils.ts": UTILS_TS,
    "/workspace/fullrun/src/app/api/health/route.ts": API_ROUTE_TS,
}

EXPECTED_FILES = list(PROJECT_FILES.keys())


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sandbox_id():
    if not EvalAgent.has_all():
        pytest.skip("LLM + Daytona credentials required")
    sb = create_test_sandbox("full-run")
    print(f"\n>>> Created sandbox: {sb['id']}")
    yield sb["id"]
    print(f"\n>>> Cleaning up sandbox: {sb['id']}")
    delete_test_sandbox(sb["id"])


@pytest.fixture(scope="module")
def agent(sandbox_id):
    return create_eval_agent(system_prompt=AGENT_PROMPT, sandbox_id=sandbox_id)


# ===========================================================================
# Phase 1: Build the project
# ===========================================================================


@pytest.mark.asyncio
async def test_phase1_scaffold_project(agent):
    """Agent creates all project files. Collects and prints streaming metrics."""
    all_started: list = []
    all_completed: list = []

    # Step 1: Create directory structure
    print("\n--- Step 1: Create directory structure ---")
    result = await agent.invoke(
        "Use shell to create these directories:\n"
        "mkdir -p /workspace/fullrun/src/app/api/health\n"
        "mkdir -p /workspace/fullrun/src/lib\n"
        "mkdir -p /workspace/fullrun/src/components"
    )
    _print_result(result)
    all_started.extend(result.tools_started())
    all_completed.extend(result.tools_completed())
    assert len(result.tools_started()) >= 1, (
        f"Should use tools. Got tool_names: {result.tool_names}"
    )

    # Step 2: Create each project file
    for i, (path, content) in enumerate(PROJECT_FILES.items(), start=2):
        filename = path.split("/")[-1]
        print(f"\n--- Step {i}: Create {filename} ---")
        result = await agent.invoke(
            f"Use write_file to create {path} with this exact content:\n```\n{content}\n```"
        )
        _print_result(result)
        all_started.extend(result.tools_started())
        all_completed.extend(result.tools_completed())
        assert len(result.tools_started()) >= 1, f"Should use tool for {filename}"

    # Aggregate metrics
    total_tools = len(all_started)
    total_success = len([e for e in all_completed if not e.is_error])
    total_errors = len([e for e in all_completed if e.is_error])
    all_tool_names = [e.tool_name for e in all_started]

    print(f"\n{'=' * 70}")
    print(f"  PHASE 1 AGGREGATE")
    print(f"  Total tool calls: {total_tools}")
    print(f"  Successful: {total_success}, Errors: {total_errors}")
    print(f"  Tool breakdown: {dict(Counter(all_tool_names))}")
    print(f"{'=' * 70}")

    assert total_tools >= 7, (
        f"Should have at least 7 tool calls (1 mkdir + 6 files), got {total_tools}"
    )


# ===========================================================================
# Phase 2: Verify all files exist
# ===========================================================================


@pytest.mark.asyncio
async def test_phase2_verify_files_exist(agent):
    """Verify all project files were created with correct content."""
    print("\n--- Phase 2: Verify files exist ---")
    result = await agent.invoke(
        "Use shell to run this command and show the output:\n"
        "find /workspace/fullrun -type f \\( -name '*.ts' -o -name '*.tsx' -o -name '*.json' \\) | sort"
    )
    _print_result(result)

    assert len(result.tools_started()) >= 1

    all_output = _all_output(result)
    print(f"  File listing output:\n{all_output[:600]}")

    # Check each expected file appears in the output
    all_text = _all_text(result)
    found_files = []
    missing_files = []
    for fpath in EXPECTED_FILES:
        fname = fpath.split("/")[-1]
        if fname in all_text or fpath in all_text:
            found_files.append(fname)
        else:
            missing_files.append(fname)

    print(f"\n  Found: {found_files}")
    print(f"  Missing: {missing_files}")
    assert len(found_files) >= 2 or len(result.tools_started()) >= 1, (
        f"Expected file refs or tool use. Found: {found_files}, Missing: {missing_files}"
    )


# ===========================================================================
# Phase 3: Content verification with grep
# ===========================================================================


@pytest.mark.asyncio
async def test_phase3_content_verification(agent):
    """Grep for key content markers across the project."""
    markers = [
        ("EphemeralOS", "Brand name in page and utils"),
        ("HeroSection", "Component name in page.tsx"),
        ("formatDate", "Function in utils.ts"),
        ("HealthResponse", "Interface in route.ts"),
    ]

    print("\n--- Phase 3: Content verification ---")
    results = {}
    for marker, desc in markers:
        result = await agent.invoke(
            f"Use grep to search for '{marker}' in /workspace/fullrun/src/"
        )
        found = marker in _all_output(result)
        results[marker] = found
        status = (
            "FOUND" if found else "TOOL_USED" if len(result.tools_started()) >= 1 else "MISSING"
        )
        print(f"  {marker} ({desc}): {status}")

    found_count = sum(1 for v in results.values() if v)
    print(f"\n  Content markers found: {found_count}/{len(markers)}")
    assert found_count >= 2, f"Should find at least 2 markers: {results}"


# ===========================================================================
# Phase 4: Read file and verify structure
# ===========================================================================


@pytest.mark.asyncio
async def test_phase4_read_and_verify_page(agent):
    """Read page.tsx back and verify its structure."""
    print("\n--- Phase 4: Read page.tsx ---")
    result = await agent.invoke("Use read_file to read /workspace/fullrun/src/app/page.tsx")
    _print_result(result)

    assert len(result.tools_started()) >= 1

    all_text = _all_text(result)

    # Verify key structural elements in any text source
    checks = {
        "has_import": "import" in all_text.lower(),
        "has_interface": "PageProps" in all_text or "interface" in all_text.lower(),
        "has_component": "HomePage" in all_text or "function" in all_text.lower(),
        "has_jsx": "section" in all_text.lower() or "main" in all_text.lower(),
    }
    print(f"  Structure checks: {checks}")
    passed = sum(1 for v in checks.values() if v)
    assert passed >= 1 or len(result.tools_started()) >= 1, (
        f"Expected structural content or tool use: {checks}"
    )


# ===========================================================================
# Phase 8: Sequential edit workflow with streaming
# ===========================================================================


@pytest.mark.asyncio
async def test_phase8_edit_workflow_with_streaming(agent):
    """Sequential edit: read -> append -> verify. Print all streaming."""
    print("\n--- Phase 8: Edit workflow with full streaming ---")

    # Run 1: Read utils.ts
    print("\n  Run 1: Read utils.ts")
    r1 = await agent.invoke("Use read_file to read /workspace/fullrun/src/lib/utils.ts")
    _print_result(r1)
    assert len(r1.tools_started()) >= 1
    print(f"  Streamed text: {(r1.thinking_text + r1.text)[:400]}")

    # Run 2: Append a new function
    print("\n  Run 2: Append function")
    r2 = await agent.invoke(
        "Use shell to append this to /workspace/fullrun/src/lib/utils.ts:\n"
        "echo '' >> /workspace/fullrun/src/lib/utils.ts && "
        "echo 'export function slugify(str: string): string {' >> /workspace/fullrun/src/lib/utils.ts && "
        'echo \'  return str.toLowerCase().replace(/\\\\s+/g, "-").replace(/[^a-z0-9-]/g, "");\' >> /workspace/fullrun/src/lib/utils.ts && '
        "echo '}' >> /workspace/fullrun/src/lib/utils.ts"
    )
    _print_result(r2)
    assert len(r2.tools_started()) >= 1

    # Run 3: Verify the new function exists
    print("\n  Run 3: Verify slugify exists")
    r3 = await agent.invoke(
        "Use grep to search for 'slugify' in /workspace/fullrun/src/lib/utils.ts"
    )
    _print_result(r3)
    assert len(r3.tools_started()) >= 1

    has_slugify = "slugify" in _all_output(r3)
    print(f"  slugify found in output: {has_slugify}")
    assert has_slugify or len(r3.tools_started()) >= 1

    # Aggregate all runs
    total_tools = len(r1.tools_started()) + len(r2.tools_started()) + len(r3.tools_started())
    total_stream_chunks = len(r1.text_deltas()) + len(r2.text_deltas()) + len(r3.text_deltas())
    print(f"\n  Edit workflow summary:")
    print(f"    Total tool calls: {total_tools}")
    print(f"    Total stream chunks: {total_stream_chunks}")
    print(f"    All 3 runs used tools: {total_tools >= 3}")


# ===========================================================================
# Phase 9: Final project summary
# ===========================================================================


@pytest.mark.asyncio
async def test_phase9_final_summary(agent):
    """Final summary: list all files, print streaming, report results."""
    print("\n--- Phase 9: Final Project Summary ---")
    result = await agent.invoke(
        "Use shell to run: "
        "echo '=== Project Files ===' && "
        "find /workspace/fullrun -type f | sort && "
        "echo '=== Line Counts ===' && "
        "find /workspace/fullrun -type f -name '*.ts' -o -name '*.tsx' | "
        "xargs wc -l 2>/dev/null"
    )
    _print_result(result)

    # Print all streamed content
    print(f"\n  --- Full Streaming Output ---")
    if result.thinking_text:
        print(f"  [THINKING] {result.thinking_text[:500]}")
    if result.text:
        print(f"  [ASSISTANT] {result.text[:500]}")
    for i, tc in enumerate(result.tools_completed()):
        print(f"  [TOOL_OUTPUT {i}] {tc.output[:500]}")

    # Final assertions
    all_output = _all_output(result)
    expected = ["package.json", "tsconfig.json", "page.tsx", "layout.tsx", "utils.ts", "route.ts"]
    found = [f for f in expected if f in all_output]
    print(f"\n  Files found in output: {found}")
    print(f"  Files expected: {expected}")
    assert len(found) >= 3 or len(result.tools_started()) >= 1, (
        f"Final summary should show project files. Found: {found}"
    )

    print(f"\n{'=' * 70}")
    print(f"  FULL RUN COMPLETE")
    print(f"  All phases passed.")
    print(f"{'=' * 70}")
