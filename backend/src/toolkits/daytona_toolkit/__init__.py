"""Daytona toolkit — sandbox execution via the Daytona SDK (no agno dependency).

Provides file I/O, shell execution, and search tools that operate inside a
remote Daytona sandbox. Requires the ``daytona`` package (``pip install daytona-sdk``).

Configuration via environment variables:
    DAYTONA_API_KEY   — authentication token
    DAYTONA_API_URL   — base URL of the Daytona orchestrator
    DAYTONA_TARGET    — target environment/region (optional)
"""

from ephemeralos.toolkits.daytona_toolkit.toolkit import DaytonaToolkit

__all__ = ["DaytonaToolkit"]
