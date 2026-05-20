"""Shared primitives for typed EphemeralOS config sections."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict
from typing_extensions import Self


def _camel_to_snake(value: str) -> str:
    value = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", value)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", value).lower()


class ModuleConfigBase(BaseModel):
    """Base class for module-owned config sections."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @classmethod
    def section_name(cls) -> str:
        """Return the default YAML section name for this config class."""
        name = cls.__name__
        if name.endswith("Config"):
            name = name[: -len("Config")]
        return _camel_to_snake(name)

    def with_overrides(self, **kwargs: Any) -> Self:
        """Return a shallow copy with non-None field overrides applied."""
        return self.model_copy(
            update={key: value for key, value in kwargs.items() if value is not None}
        )
