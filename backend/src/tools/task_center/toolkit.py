"""Task Center toolkit — post and read notes shared between agents."""

from __future__ import annotations

from pydantic import BaseModel, Field

from tools.core.base import BaseTool, BaseToolkit, ToolExecutionContext, ToolResult


# ---------------------------------------------------------------------------
# PostNoteTool
# ---------------------------------------------------------------------------


class PostNoteInput(BaseModel):
    content: str = Field(..., description="Note content to post", min_length=1)
    scope_paths: list[str] | None = Field(
        default=None, description="File/dir scope for filtering"
    )


class PostNoteTool(BaseTool):
    name = "post_note"
    description = "Post a note to the Task Center for other agents to read."
    input_model = PostNoteInput

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        assert isinstance(arguments, PostNoteInput)
        from team.models import Note
        import time
        import uuid

        tc = context.metadata.get("task_center")
        if tc is None:
            return ToolResult(output="Error: Task Center not available", is_error=True)
        scope = arguments.scope_paths or list(context.metadata.get("write_scope") or [])
        note = Note(
            id=str(uuid.uuid4()),
            task_id=context.metadata.get("work_item_id", ""),
            agent_name=context.metadata.get("agent_name", ""),
            content=arguments.content,
            timestamp=time.time(),
            scope_paths=scope,
        )
        tc.post(note)
        return ToolResult(output=f"Note posted ({len(arguments.content)} chars).")


# ---------------------------------------------------------------------------
# ReadNotesTool
# ---------------------------------------------------------------------------


class ReadNotesInput(BaseModel):
    authors: list[str] | None = Field(
        default=None, description="Filter by task IDs that authored the notes"
    )
    scope_paths: list[str] | None = Field(
        default=None, description="Filter by scope path prefix"
    )
    limit: int | None = Field(default=None, description="Max notes to return")


class ReadNotesTool(BaseTool):
    name = "read_notes"
    description = "Read notes from the Task Center, optionally filtered."
    input_model = ReadNotesInput

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        assert isinstance(arguments, ReadNotesInput)

        tc = context.metadata.get("task_center")
        if tc is None:
            return ToolResult(output="Error: Task Center not available", is_error=True)
        notes = tc.read(
            authors=arguments.authors,
            scope_paths=arguments.scope_paths,
            limit=arguments.limit,
        )
        if not notes:
            return ToolResult(output="No notes found.")
        lines: list[str] = []
        for n in notes:
            header = f"### {n.agent_name} ({n.task_id})"
            if n.scope_paths:
                header += f" [scope: {', '.join(n.scope_paths)}]"
            lines.append(header)
            lines.append(n.content)
            lines.append("")
        return ToolResult(output="\n".join(lines))


# ---------------------------------------------------------------------------
# Toolkits
# ---------------------------------------------------------------------------


class TaskCenterReadToolkit(BaseToolkit):
    """Read-only access to Task Center notes."""

    @classmethod
    def from_context(cls, ctx: object) -> TaskCenterReadToolkit:
        return cls(
            name="task_center_read",
            description="Read notes from the Task Center.",
            tools=[ReadNotesTool()],
        )


class TaskCenterWriteToolkit(BaseToolkit):
    """Full read/write access to Task Center notes."""

    @classmethod
    def from_context(cls, ctx: object) -> TaskCenterWriteToolkit:
        return cls(
            name="task_center_write",
            description="Post and read notes in the Task Center.",
            tools=[PostNoteTool(), ReadNotesTool()],
        )
