"""Delegated workflow tools."""

from __future__ import annotations

from tools._framework.core.base import BaseTool
from tools.workflow.cancel_workflow import cancel_workflow
from tools.workflow.check_workflow_status import check_workflow_status
from tools.workflow.delegate_workflow import delegate_workflow


def make_workflow_tools() -> list[BaseTool]:
    return [delegate_workflow, check_workflow_status, cancel_workflow]


__all__ = [
    "cancel_workflow",
    "check_workflow_status",
    "delegate_workflow",
    "make_workflow_tools",
]
