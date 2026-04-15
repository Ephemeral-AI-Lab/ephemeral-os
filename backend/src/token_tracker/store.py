"""Token usage tracking store."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from sqlalchemy import func

from db.stores.base import SyncStoreMixin
from token_tracker.models import TokenUsageRecord

logger = logging.getLogger(__name__)


class UsageStore(SyncStoreMixin):
    """Records and queries token consumption."""

    def record(
        self,
        *,
        session_id: str,
        run_id: str | None = None,
        agent_name: str,
        model_id: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> TokenUsageRecord:
        with self._sf() as db:
            rec = TokenUsageRecord(
                session_id=session_id,
                run_id=run_id,
                agent_name=agent_name,
                model_id=model_id,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            )
            db.add(rec)
            db.commit()
            db.refresh(rec)
            return rec

    def get_session_usage(self, session_id: str) -> dict:
        """Aggregate token usage for a session."""
        with self._sf() as db:
            row = (
                db.query(
                    func.coalesce(func.sum(TokenUsageRecord.prompt_tokens), 0),
                    func.coalesce(func.sum(TokenUsageRecord.completion_tokens), 0),
                    func.coalesce(func.sum(TokenUsageRecord.total_tokens), 0),
                    func.count(TokenUsageRecord.id),
                )
                .filter(TokenUsageRecord.session_id == session_id)
                .one()
            )
            return {
                "session_id": session_id,
                "prompt_tokens": row[0],
                "completion_tokens": row[1],
                "total_tokens": row[2],
                "run_count": row[3],
                "call_count": row[3],
            }

    def get_usage_by_model(self, session_id: str | None = None) -> list[dict]:
        """Break down usage by model, optionally filtered by session."""
        with self._sf() as db:
            q = db.query(
                TokenUsageRecord.model_id,
                func.sum(TokenUsageRecord.prompt_tokens),
                func.sum(TokenUsageRecord.completion_tokens),
                func.sum(TokenUsageRecord.total_tokens),
                func.count(TokenUsageRecord.id),
            ).group_by(TokenUsageRecord.model_id)
            if session_id:
                q = q.filter(TokenUsageRecord.session_id == session_id)
            return [
                {
                    "model_id": row[0],
                    "prompt_tokens": row[1],
                    "completion_tokens": row[2],
                    "total_tokens": row[3],
                    "run_count": row[4],
                    "call_count": row[4],
                }
                for row in q.all()
            ]

    def get_run_usage(self, run_id: str) -> dict | None:
        """Return aggregated usage for a single run."""
        return self.get_usage_for_runs([run_id]).get(run_id)

    def get_usage_for_runs(self, run_ids: Iterable[str]) -> dict[str, dict]:
        """Return a mapping of ``run_id -> usage summary`` for the given runs."""
        normalized = [run_id for run_id in dict.fromkeys(run_ids) if run_id]
        if not normalized:
            return {}

        with self._sf() as db:
            rows = (
                db.query(TokenUsageRecord)
                .filter(TokenUsageRecord.run_id.in_(normalized))
                .order_by(TokenUsageRecord.id.asc())
                .all()
            )

        usage_by_run: dict[str, dict] = {}
        for row in rows:
            if row.run_id is None:
                continue
            if row.run_id not in usage_by_run:
                usage_by_run[row.run_id] = {
                    "run_id": row.run_id,
                    "model_id": row.model_id,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                }
            usage = usage_by_run[row.run_id]
            usage["prompt_tokens"] += row.prompt_tokens
            usage["completion_tokens"] += row.completion_tokens
            usage["total_tokens"] += row.total_tokens
        return usage_by_run
