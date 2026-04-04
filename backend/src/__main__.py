"""Allow running as ``python -m ephemeralos``."""

import asyncio

from ephemeralos.server.entrypoint import run_web

if __name__ == "__main__":
    asyncio.run(run_web(open_browser=False))
