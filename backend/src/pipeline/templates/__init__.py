"""Bundled pipeline templates."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pipeline.schema import PipelineConfig

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent


def load_bundled_templates() -> list[PipelineConfig]:
    """Load all bundled pipeline templates from JSON files."""
    templates: list[PipelineConfig] = []
    for path in sorted(_TEMPLATES_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text())
            templates.append(PipelineConfig.model_validate(data))
        except Exception:
            logger.warning("Failed to load template %s", path, exc_info=True)
    return templates
