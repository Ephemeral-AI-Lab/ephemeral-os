"""Allow running as ``python -m ephemeralos``."""

import asyncio

from ephemeralos.ui.app import run_web

asyncio.run(run_web(open_browser=False))
