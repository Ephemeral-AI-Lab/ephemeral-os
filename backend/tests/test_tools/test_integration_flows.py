"""Higher-level integration flows across multiple built-in tools."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tools import create_default_tool_registry
from tools.base import ToolExecutionContext


@pytest.mark.asyncio
async def test_search_edit_flow_across_registry(tmp_path: Path):
    registry = create_default_tool_registry()
    context = ToolExecutionContext(cwd=tmp_path, metadata={"tool_registry": registry})

    write = registry.get("write_file")
    glob = registry.get("glob")
    grep = registry.get("grep")
    edit = registry.get("edit_file")
    read = registry.get("read_file")

    await write.execute(
        write.input_model(path="src/demo.py", content="alpha\nbeta\n"),
        context,
    )
    glob_result = await glob.execute(glob.input_model(pattern="**/*.py"), context)
    assert "src/demo.py" in glob_result.output

    grep_result = await grep.execute(
        grep.input_model(pattern="beta", file_glob="**/*.py"),
        context,
    )
    assert "src/demo.py:2:beta" in grep_result.output

    await edit.execute(
        edit.input_model(path="src/demo.py", old_str="beta", new_str="gamma"),
        context,
    )
    read_result = await read.execute(read.input_model(path="src/demo.py"), context)
    assert "gamma" in read_result.output
    assert "beta" not in (tmp_path / "src" / "demo.py").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_task_and_todo_flow_across_registry(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("EPHEMERALOS_DATA_DIR", str(tmp_path / "data"))
    registry = create_default_tool_registry()
    context = ToolExecutionContext(cwd=tmp_path, metadata={"tool_registry": registry})

    tool_search = registry.get("tool_search")
    todo_write = registry.get("todo_write")
    task_create = registry.get("task_create")
    task_get = registry.get("task_get")
    task_output = registry.get("task_output")
    task_update = registry.get("task_update")

    search_result = await tool_search.execute(tool_search.input_model(query="task"), context)
    assert "task_create" in search_result.output

    await todo_write.execute(todo_write.input_model(item="integration flow item"), context)
    assert "integration flow item" in (tmp_path / "TODO.md").read_text(encoding="utf-8")

    create_result = await task_create.execute(
        task_create.input_model(
            type="local_bash",
            description="integration flow task",
            command="printf 'INTEGRATION_TASK_OK'",
        ),
        context,
    )
    task_id = create_result.output.split()[2]
    update_result = await task_update.execute(
        task_update.input_model(
            task_id=task_id,
            progress=25,
            status_note="started",
        ),
        context,
    )
    assert "progress=25%" in update_result.output

    task_detail = await task_get.execute(task_get.input_model(task_id=task_id), context)
    assert "'progress': '25'" in task_detail.output
    assert "'status_note': 'started'" in task_detail.output

    for _ in range(20):
        output = await task_output.execute(task_output.input_model(task_id=task_id), context)
        if "INTEGRATION_TASK_OK" in output.output:
            break
        await asyncio.sleep(0.1)
    else:
        raise AssertionError("task output did not become available in time")

    assert "INTEGRATION_TASK_OK" in output.output


@pytest.mark.asyncio
async def test_skill_and_config_flow_across_registry(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("EPHEMERALOS_CONFIG_DIR", str(tmp_path / "config"))
    skills_dir = tmp_path / "config" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "pytest.md").write_text(
        "# Pytest\nPytest fixtures help reuse setup.\n",
        encoding="utf-8",
    )

    registry = create_default_tool_registry()
    context = ToolExecutionContext(cwd=tmp_path, metadata={"tool_registry": registry})

    config = registry.get("config")
    skill = registry.get("skill")

    set_result = await config.execute(
        config.input_model(action="set", key="theme", value="night-owl"),
        context,
    )
    assert set_result.output == "Updated theme"

    show_result = await config.execute(config.input_model(action="show"), context)
    assert "night-owl" in show_result.output

    skill_result = await skill.execute(skill.input_model(name="Pytest"), context)
    assert "fixtures" in skill_result.output


@pytest.mark.asyncio
async def test_ask_user_question_flow_across_registry(tmp_path: Path):
    registry = create_default_tool_registry()

    async def _answer(question: str) -> str:
        assert "favorite color" in question
        return "green"

    context = ToolExecutionContext(
        cwd=tmp_path,
        metadata={"tool_registry": registry, "ask_user_prompt": _answer},
    )
    ask_user = registry.get("ask_user_question")
    write = registry.get("write_file")
    read = registry.get("read_file")

    answer_result = await ask_user.execute(
        ask_user.input_model(question="What is your favorite color?"),
        context,
    )
    assert answer_result.output == "green"

    await write.execute(
        write.input_model(path="answer.txt", content=answer_result.output),
        context,
    )
    read_result = await read.execute(read.input_model(path="answer.txt"), context)
    assert "green" in read_result.output




@pytest.mark.asyncio
async def test_lsp_flow_across_registry(tmp_path: Path):
    registry = create_default_tool_registry()
    context = ToolExecutionContext(cwd=tmp_path, metadata={"tool_registry": registry})

    write = registry.get("write_file")
    lsp = registry.get("lsp")

    await write.execute(
        write.input_model(
            path="pkg/utils.py",
            content='def greet(name):\n    """Return a greeting."""\n    return f"hi {name}"\n',
        ),
        context,
    )
    await write.execute(
        write.input_model(
            path="pkg/app.py",
            content="from pkg.utils import greet\n\nprint(greet('world'))\n",
        ),
        context,
    )

    symbol_result = await lsp.execute(
        lsp.input_model(operation="workspace_symbol", query="greet"),
        context,
    )
    assert "function greet" in symbol_result.output

    definition_result = await lsp.execute(
        lsp.input_model(operation="go_to_definition", file_path="pkg/app.py", symbol="greet"),
        context,
    )
    assert "pkg/utils.py:1:1" in definition_result.output

    hover_result = await lsp.execute(
        lsp.input_model(operation="hover", file_path="pkg/app.py", symbol="greet"),
        context,
    )
    assert "Return a greeting." in hover_result.output
