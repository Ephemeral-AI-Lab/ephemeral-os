"""Package for the `submit_workflow_handoff` tool."""

from . import submit_workflow_handoff as _impl

submit_workflow_handoff = _impl.submit_workflow_handoff
SubmitWorkflowHandoffInput = _impl.SubmitWorkflowHandoffInput

__all__ = ["SubmitWorkflowHandoffInput", "submit_workflow_handoff"]
