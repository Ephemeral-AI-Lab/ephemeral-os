"""System toolkit — configuration and utility tools."""

from ephemeralos.tools.base import BaseToolkit
from ephemeralos.tools.system.brief_tool import BriefTool
from ephemeralos.tools.system.config_tool import ConfigTool
from ephemeralos.tools.system.remote_trigger_tool import RemoteTriggerTool
from ephemeralos.tools.system.sleep_tool import SleepTool


class SystemToolkit(BaseToolkit):
    """System utilities: config, brief mode, sleep, remote triggers."""

    def __init__(self) -> None:
        super().__init__(
            name="system",
            description="System utilities: config, brief mode, sleep, remote triggers",
            tools=[ConfigTool(), BriefTool(), SleepTool(), RemoteTriggerTool()],
        )


__all__ = [
    "SystemToolkit",
    "ConfigTool",
    "BriefTool",
    "SleepTool",
    "RemoteTriggerTool",
]
