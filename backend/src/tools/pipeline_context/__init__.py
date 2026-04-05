"""Pipeline context toolkit — query the incremental context map during pipeline execution."""

from tools.base import BaseToolkit
from tools.pipeline_context.context_tools import (
    GetPipelineMetadataTool,
    ListPipelineStepsTool,
    QueryPipelineContextTool,
)


class PipelineContextToolkit(BaseToolkit):
    """Tools for querying pipeline context map and metadata."""

    def __init__(
        self,
        *,
        context_map: dict[str, dict] | None = None,
        pipeline_meta: dict | None = None,
        current_step: str | None = None,
    ) -> None:
        super().__init__(
            name="pipeline_context",
            description="Query pipeline context map and metadata",
            tools=[
                QueryPipelineContextTool(context_map=context_map),
                ListPipelineStepsTool(context_map=context_map),
                GetPipelineMetadataTool(
                    pipeline_meta=pipeline_meta,
                    current_step=current_step,
                ),
            ],
        )


__all__ = ["PipelineContextToolkit"]
