"""Generic process-global registry shared by predicates and recipes.

Both ``PredicateRegistry`` and ``RecipeRegistry`` are class-state singletons
with near-identical contracts. This module exposes a single
:class:`Registry[T]` base so the duplication shrinks to a one-line override
per subclass.
"""

from __future__ import annotations

from typing import Any, ClassVar, Generic, TypeVar

T = TypeVar("T")


class Registry(Generic[T]):
    """Process-global classmethod registry base class.

    Subclasses must declare ``_registry: ClassVar[dict[str, T]] = {}``
    and override :meth:`register` if they derive the key from the value
    (e.g. ``RecipeRegistry`` indexes by ``recipe.id``).

    Tests call :meth:`clear` in teardown when they register ad-hoc items.
    """

    # Override on subclass with an empty dict typed for ``T``.
    _registry: ClassVar[dict[str, Any]]

    # Override on subclass to customise the lookup-miss exception. Defaults
    # to ``KeyError`` so the contract matches Python's mapping protocol.
    _missing_exc: ClassVar[type[Exception]] = KeyError

    @classmethod
    def get(cls, key: str) -> T:
        try:
            return cls._registry[key]
        except KeyError as exc:
            raise cls._missing_exc(
                f"{cls.__name__}: {key!r} is not registered. "
                f"Known: {sorted(cls._registry)!r}"
            ) from exc

    @classmethod
    def has(cls, key: str) -> bool:
        return key in cls._registry

    @classmethod
    def list_ids(cls) -> list[str]:
        return sorted(cls._registry)

    @classmethod
    def clear(cls) -> None:
        cls._registry.clear()

    @classmethod
    def _put(cls, key: str, value: T) -> None:
        cls._registry[key] = value


__all__ = ["Registry"]
