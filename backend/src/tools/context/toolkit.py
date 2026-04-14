"""Task Center tools — notes + staleness.

Tools exposed in the main loop:
- read_notes               — read/search notes with optional keyword filter
- context_changed_since    — check if context is stale (other agents' edits)

`post_note` is still defined in this module, but is exposed via the post-run and
external-trigger phases rather than the main-loop context toolkit.

Role-based restrictions are handled via ``blocked_tools`` in agent definitions
rather than separate read/write toolkit variants.
"""

from __future__ import annotations

import json
import re
import time
import uuid

from pydantic import BaseModel, Field

from team._path_utils import normalize_scope_paths, scope_paths_overlap
from tools.context.freshness import check_freshness
from tools.core.base import BaseTool, BaseToolkit, ToolExecutionContext, ToolResult

_BACKTICK_PATH_RE = re.compile(r"`([^`\n]+)`")


def _scout_scope_repair_paths(content: str, note_paths: list[str]) -> list[str]:
    if "does not exist" not in content.lower():
        return []
    leaked: list[str] = []
    for token in _BACKTICK_PATH_RE.findall(content):
        candidate = token.strip().replace("\\", "/").rstrip("/")
        if "/" not in candidate or " " in candidate:
            continue
        if any(scope_paths_overlap(candidate, allowed) for allowed in note_paths):
            continue
        leaked.append(candidate)
    return normalize_scope_paths(leaked)


def _sanitize_scout_gap_paths(content: str, note_paths: list[str]) -> str:
    leaked = set(_scout_scope_repair_paths(content, note_paths))
    if not leaked:
        return content

    def _rewrite(match: re.Match[str]) -> str:
        token = match.group(1).strip().replace("\\", "/").rstrip("/")
        return token if token in leaked else match.group(0)

    return _BACKTICK_PATH_RE.sub(_rewrite, content)


# ---------------------------------------------------------------------------
# PostNoteTool
# ---------------------------------------------------------------------------


class PostNoteInput(BaseModel):
    content: str = Field(..., description="Note content to post", min_length=1)
    paths: list[str] | None = Field(
        default=None,
        description=(
            "File/dir paths this note relates to. Can be existing or planned paths. "
            "If omitted, defaults to the task's write_scope. Other agents can find "
            "this note via read_notes(paths=[...])."
        ),
    )
    tags: list[str] | None = Field(
        default=None,
        description=(
            "Classify the note with one or more tags: discovery, implementation, "
            "bug_fix, blocker, proposal, verification, architecture, dependency, "
            "warning, refactor. Use 'proposal' for notes about paths not yet created."
        ),
    )
    parent_note_id: str | None = Field(
        default=None,
        description="ID of a prior note this is a follow-up to (threading).",
    )


class PostNoteTool(BaseTool):
    name = "post_note"
    description = (
        "Post a note to the Task Center for other agents to read. "
        "Use for: blockers that siblings should know about, partial progress "
        "updates on long tasks, discoveries about the codebase that downstream "
        "tasks need, and exploration findings (scouts). Notes are append-only "
        "and immutable — post a new note to update, don't try to edit."
    )
    input_model = PostNoteInput
    tool_types = frozenset({"external_trigger", "post_run"})

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        assert isinstance(arguments, PostNoteInput)
        from team.models import Note, NoteTag

        tc = context.metadata.get("task_center")
        if tc is None:
            return ToolResult(output="Error: Task Center not available", is_error=True)

        # Validate tags
        if arguments.tags:
            valid_tags = {t.value for t in NoteTag}
            invalid = [t for t in arguments.tags if t not in valid_tags]
            if invalid:
                return ToolResult(
                    output=f"Invalid tag(s): {invalid}. Valid tags: {sorted(valid_tags)}",
                    is_error=True,
                )

        content = arguments.content
        note_paths = normalize_scope_paths(
            arguments.paths or list(context.metadata.get("write_scope") or [])
        )
        if str(context.metadata.get("agent_name") or "").strip() == "scout" and note_paths:
            if "intended path" not in content.lower() and "correct path" not in content.lower():
                content = _sanitize_scout_gap_paths(content, note_paths)
            repaired = _scout_scope_repair_paths(content, note_paths)
            if repaired:
                return ToolResult(
                    output=(
                        "Scout note scope guard: keep missing targets missing. "
                        "Do not rename them to nearby paths such as "
                        f"{', '.join(repaired[:3])}."
                    ),
                    is_error=True,
                )
        note = Note(
            id=str(uuid.uuid4()),
            task_id=context.metadata.get("work_item_id", ""),
            agent_name=context.metadata.get("agent_name", ""),
            content=content,
            timestamp=time.time(),
            paths=note_paths,
            tags=list(arguments.tags or []),
            parent_note_id=arguments.parent_note_id,
        )
        await tc.notes.post(note)
        return ToolResult(output=f"Note posted ({len(content)} chars).")


# ---------------------------------------------------------------------------
# ReadNotesTool — absorbs former search_context via optional keyword param
# ---------------------------------------------------------------------------


class ReadNotesInput(BaseModel):
    paths: list[str] | None = Field(
        default=None,
        description=(
            "Filter by path prefix — returns notes whose paths overlap "
            "with these prefixes (e.g. 'src/auth/' matches 'src/auth/session.py'). "
            "Omit to return all notes."
        ),
    )
    tags: list[str] | None = Field(
        default=None,
        description=(
            "Filter by tag (OR semantics). Valid tags: discovery, implementation, "
            "bug_fix, blocker, proposal, verification, architecture, dependency, "
            "warning, refactor. Omit to return all tags."
        ),
    )
    keyword: str | None = Field(
        default=None,
        description=(
            "Keyword filter (case-insensitive substring match on note content). "
            "Use '|' to separate multiple keywords for OR matching, "
            "e.g. 'session|token' matches notes containing either word."
        ),
    )
    parent_note_id: str | None = Field(
        default=None, description="Filter to notes that are replies to this note ID."
    )
    last_n: int | None = Field(default=None, description="Return only the N most recent matching notes.")


class ReadNotesTool(BaseTool):
    name = "read_notes"
    description = (
        "Read notes from the Task Center. ALWAYS include paths=[<your_scope_paths>] "
        "to scope reads to relevant files — unfiltered reads waste context and miss nothing useful. "
        "Also use tags= to find specific note types (e.g. 'blocker', 'discovery') "
        "and keyword= for text search. "
        "Call as your FIRST tool on every fresh developer or validator lane."
    )
    input_model = ReadNotesInput

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        assert isinstance(arguments, ReadNotesInput)
        from team.models import NoteTag

        tc = context.metadata.get("task_center")
        if tc is None:
            return ToolResult(output="Error: Task Center not available", is_error=True)

        # Validate tags
        if arguments.tags:
            valid_tags = {t.value for t in NoteTag}
            invalid = [t for t in arguments.tags if t not in valid_tags]
            if invalid:
                return ToolResult(
                    output=f"Invalid tag(s): {invalid}. Valid tags: {sorted(valid_tags)}",
                    is_error=True,
                )

        # Validate paths — check if any notes match
        if arguments.paths:
            matched = await tc.notes.read(paths=arguments.paths)
            if not matched:
                known = tc.notes.known_paths()
                return ToolResult(
                    output=(
                        f"No notes found for paths: {arguments.paths}. "
                        f"Known note paths: {known}"
                    ),
                    is_error=True,
                )

        notes = await tc.notes.read_notes(
            paths=arguments.paths,
            tags=arguments.tags,
            keyword=arguments.keyword,
            last_n=arguments.last_n,
            parent_note_id=arguments.parent_note_id,
        )
        if not notes:
            return ToolResult(output="No notes found.")
        lines: list[str] = []
        for n in notes:
            header = f"### {n.agent_name} ({n.task_id})"
            if n.paths:
                header += f" [paths: {', '.join(n.paths)}]"
            if n.tags:
                header += f" [tags: {', '.join(n.tags)}]"
            lines.append(header)
            lines.append(n.content)
            lines.append("")
        return ToolResult(output="\n".join(lines))


# ---------------------------------------------------------------------------
# ReadSiblingNotesTool
# ---------------------------------------------------------------------------


class ReadSiblingNotesInput(BaseModel):
    paths: list[str] | None = Field(
        default=None,
        description=(
            "Filter sibling notes by path prefix. "
            "Omit to return all sibling notes."
        ),
    )
    tags: list[str] | None = Field(
        default=None,
        description=(
            "Optional tag filter (OR semantics). Valid tags: discovery, implementation, "
            "bug_fix, blocker, proposal, verification, architecture, dependency, "
            "warning, refactor. Omit to return all tags."
        ),
    )
    keyword: str | None = Field(
        default=None,
        description=(
            "Keyword filter (case-insensitive substring match on note content). "
            "Use '|' to separate multiple keywords for OR matching, "
            "e.g. 'session|token' matches notes containing either word."
        ),
    )
    last_n: int | None = Field(default=None, description="Return only the N most recent matching notes.")


class ReadSiblingNotesTool(BaseTool):
    name = "read_sibling_notes"
    description = (
        "Read notes from sibling tasks and their descendants. Developers MUST call this "
        "before any edit outside original scope_paths and after any verification failure "
        "to check if siblings hit the same issue. Replanners MUST call this before choosing "
        "an action. Include paths=[...] to scope to relevant files."
    )
    input_model = ReadSiblingNotesInput

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        assert isinstance(arguments, ReadSiblingNotesInput)
        from team.models import NoteTag

        tc = context.metadata.get("task_center")
        if tc is None:
            return ToolResult(output="Error: Task Center not available", is_error=True)

        task_id = str(context.metadata.get("work_item_id") or "")
        if not task_id:
            return ToolResult(output="Error: no task context available", is_error=True)

        # Validate tags
        if arguments.tags:
            valid_tags = {t.value for t in NoteTag}
            invalid = [t for t in arguments.tags if t not in valid_tags]
            if invalid:
                return ToolResult(
                    output=f"Invalid tag(s): {invalid}. Valid tags: {sorted(valid_tags)}",
                    is_error=True,
                )

        notes = await tc.notes.read_sibling_notes(
            task_id=task_id,
            paths=arguments.paths,
            tags=arguments.tags,
            keyword=arguments.keyword,
            last_n=arguments.last_n,
        )
        if not notes:
            return ToolResult(output="No sibling notes found.")
        lines: list[str] = []
        for n in notes:
            header = f"### {n.agent_name} ({n.task_id})"
            if n.paths:
                header += f" [paths: {', '.join(n.paths)}]"
            if n.tags:
                header += f" [tags: {', '.join(n.tags)}]"
            lines.append(header)
            lines.append(n.content)
            lines.append("")
        return ToolResult(output="\n".join(lines))


# ---------------------------------------------------------------------------
# ContextChangedSinceTool
# ---------------------------------------------------------------------------


class ContextChangedSinceInput(BaseModel):
    pass  # No arguments needed — uses task start time


class ContextChangedSinceTool(BaseTool):
    name = "context_changed_since"
    description = "Check if your context has changed since task started. Call before committing multi-file changes."
    input_model = ContextChangedSinceInput

    async def execute(
        self, arguments: ContextChangedSinceInput, context: ToolExecutionContext
    ) -> ToolResult:
        context.metadata["checked_context_freshness"] = True
        report = await check_freshness(context)
        # Update the freshness baseline so subsequent checks (e.g. in
        # post_note posthook) only report changes since THIS check,
        # not since work_item_started_at.  Fixes the monotonic-count bug
        # where sibling completions accumulate across the entire run.
        import time as _time
        context.metadata["freshness_checked_at"] = _time.time()
        return ToolResult(
            output=json.dumps(
                {
                    "stale": report.stale,
                    "scope_changes_by_others": report.scope_changes_by_others,
                    "new_dep_notes": report.new_dep_notes,
                    "new_sibling_completions": report.new_sibling_completions,
                    "suggestion": "Re-read affected files and check Task Center "
                    "for new context before committing."
                    if report.stale
                    else None,
                }
            )
        )


# ---------------------------------------------------------------------------
# Toolkit
# ---------------------------------------------------------------------------

_ALL_TOOLS = [
    ReadNotesTool(),
    ReadSiblingNotesTool(),
    ContextChangedSinceTool(),
]


class TaskCenterToolkit(BaseToolkit):
    """Task Center notes and scope change queries.

    All tools are registered; role-based restrictions (e.g. blocking
    ``post_note`` for planners) are handled via ``blocked_tools`` in
    agent definitions.
    """

    @classmethod
    def from_context(cls, ctx: object) -> TaskCenterToolkit:
        return cls(
            name="task_center",
            description="Read notes and check scope changes.",
            tools=list(_ALL_TOOLS),
        )
