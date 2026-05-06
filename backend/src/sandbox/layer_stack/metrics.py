"""Metrics contracts for layer-stack snapshot materialization."""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class LowerdirCacheMetrics:
    hits: int = 0
    misses: int = 0
    materialized_bytes: int = 0
    materialize_calls: int = 0
    materialize_total_s: float = 0.0
    last_lookup_s: float = 0.0
    last_materialize_s: float = 0.0

    def record_hit(self, *, lookup_s: float) -> "LowerdirCacheMetrics":
        return replace(
            self,
            hits=self.hits + 1,
            last_lookup_s=lookup_s,
            last_materialize_s=0.0,
        )

    def record_miss(
        self,
        *,
        lookup_s: float,
        materialize_s: float,
        byte_count: int,
    ) -> "LowerdirCacheMetrics":
        return replace(
            self,
            misses=self.misses + 1,
            materialized_bytes=self.materialized_bytes + byte_count,
            materialize_calls=self.materialize_calls + 1,
            materialize_total_s=self.materialize_total_s + materialize_s,
            last_lookup_s=lookup_s,
            last_materialize_s=materialize_s,
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "lowerdir_cache_hits": float(self.hits),
            "lowerdir_cache_misses": float(self.misses),
            "lowerdir_cache_materialized_bytes": float(self.materialized_bytes),
            "lowerdir_cache_materialize_calls": float(self.materialize_calls),
            "lowerdir_cache_materialize_total_s": self.materialize_total_s,
            "lowerdir_cache_last_lookup_s": self.last_lookup_s,
            "lowerdir_cache_last_materialize_s": self.last_materialize_s,
        }


__all__ = ["LowerdirCacheMetrics"]
