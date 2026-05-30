"""Model registration store — CRUD + seed for LLM model configs."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, UTC
from typing import Any

from sqlalchemy.orm import Session

from db.models.model_registration import ModelRegistrationRecord
from db.stores.base import SyncStoreMixin

logger = logging.getLogger(__name__)

_SECRET_MARKERS = ("api_key", "auth_token", "access_token", "secret", "password", "authorization")
_MODEL_ID_KEYS = ("model", "id", "model_id")


def _resolve_env_placeholders(value: Any) -> Any:
    """Resolve environment variable placeholders in kwargs values.

    Supports:
      - ``"env:VAR_NAME"``
      - ``"${VAR_NAME}"`` or ``"$VAR_NAME"``
    """
    if isinstance(value, str):
        if value.startswith("env:"):
            return os.environ.get(value[4:], "")
        m = re.fullmatch(r"\$\{(\w+)\}|\$(\w+)", value)
        if m:
            var = m.group(1) or m.group(2)
            return os.environ.get(var, "")
        return value
    if isinstance(value, dict):
        return {k: _resolve_env_placeholders(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_placeholders(v) for v in value]
    return value


def _redact_secrets(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Redact secret values for safe serialization (e.g. to frontend)."""
    redacted: dict[str, Any] = {}
    for k, v in kwargs.items():
        if any(marker in k.lower() for marker in _SECRET_MARKERS):
            if isinstance(v, str) and (v.startswith("env:") or v.startswith("$")):
                redacted[k] = v  # Keep placeholders visible
            else:
                redacted[k] = "***"
        elif isinstance(v, dict):
            redacted[k] = _redact_secrets(v)
        else:
            redacted[k] = v
    return redacted


def _to_dict(row: ModelRegistrationRecord, *, redact: bool = False) -> dict[str, Any]:
    """Convert a record to a plain dict."""
    try:
        kwargs = json.loads(row.kwargs_json) if row.kwargs_json else {}
    except (json.JSONDecodeError, TypeError):
        kwargs = {}

    if redact:
        kwargs = _redact_secrets(kwargs)

    return {
        "id": row.id,
        "key": row.key,
        "label": row.label,
        "class_path": row.class_path,
        "kwargs": kwargs,
        "is_active": row.is_active,
        "model_id": _first_present(kwargs, _MODEL_ID_KEYS),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _first_present(kwargs: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in kwargs:
            return kwargs[key]
    return None


class ModelStore(SyncStoreMixin):
    """CRUD operations for model registrations."""

    _store_label = "ModelStore"

    # -- writes ----------------------------------------------------------------

    def register(
        self,
        *,
        key: str,
        label: str,
        class_path: str,
        kwargs: dict[str, Any] | None = None,
        activate: bool = False,
    ) -> dict[str, Any]:
        """Create or update a model registration."""
        kwargs_str = json.dumps(kwargs or {})
        now = datetime.now(UTC)

        with self._sf() as db:
            existing = db.query(ModelRegistrationRecord).filter_by(key=key).first()
            if existing is not None:
                existing.label = label
                existing.class_path = class_path
                existing.kwargs_json = kwargs_str
                existing.updated_at = now
                if activate:
                    self._deactivate_all(db)
                    existing.is_active = True
                db.commit()
                db.refresh(existing)
                return _to_dict(existing)

            if activate:
                self._deactivate_all(db)

            record = ModelRegistrationRecord(
                key=key,
                label=label,
                class_path=class_path,
                kwargs_json=kwargs_str,
                is_active=activate,
                created_at=now,
                updated_at=now,
            )
            db.add(record)
            db.commit()
            db.refresh(record)
            return _to_dict(record)

    def delete(self, key: str) -> bool:
        """Delete a model. If it was active, activate the first remaining."""
        with self._sf() as db:
            record = db.query(ModelRegistrationRecord).filter_by(key=key).first()
            if record is None:
                return False
            was_active = record.is_active
            db.delete(record)
            db.commit()
            if was_active:
                first = (
                    db.query(ModelRegistrationRecord)
                    .order_by(
                        ModelRegistrationRecord.created_at.asc(),
                        ModelRegistrationRecord.id.asc(),
                    )
                    .first()
                )
                if first is not None:
                    first.is_active = True
                    db.commit()
            return True

    # -- reads -----------------------------------------------------------------

    def get(self, key: str, *, redact: bool = True) -> dict[str, Any] | None:
        """Get a model by key."""
        with self._sf() as db:
            record = db.query(ModelRegistrationRecord).filter_by(key=key).first()
            if record is None:
                return None
            return _to_dict(record, redact=redact)

    def get_active(self, *, redact: bool = True) -> dict[str, Any] | None:
        """Get the currently active model."""
        with self._sf() as db:
            record = db.query(ModelRegistrationRecord).filter_by(is_active=True).first()
            if record is None:
                return None
            return _to_dict(record, redact=redact)

    def get_active_resolved(self) -> dict[str, Any] | None:
        """Get the active model with env placeholders resolved (for instantiation)."""
        entry = self.get_active(redact=False)
        if entry is None:
            return None
        entry["kwargs"] = _resolve_env_placeholders(entry["kwargs"])
        return entry

    # -- seed ------------------------------------------------------------------

    def seed_from_json(self, json_path: str) -> int:
        """Seed the DB from a registry.json file. Only runs if DB is empty."""
        with self._sf() as db:
            count = db.query(ModelRegistrationRecord).count()
            if count > 0:
                logger.info("ModelStore already has %d entries — skipping seed", count)
                return 0

        try:
            with open(json_path, encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            logger.warning("Cannot read seed file %s: %s", json_path, exc)
            return 0

        models = data.get("models", [])
        active_key = data.get("active", "")
        imported = 0

        for entry in models:
            key = entry.get("key", "")
            if not key:
                continue
            factory = entry.get("factory", entry)
            self.register(
                key=key,
                label=entry.get("label", key),
                class_path=factory.get("class_path", ""),
                kwargs=factory.get("kwargs", {}),
                activate=(key == active_key),
            )
            imported += 1

        logger.info("Seeded %d model(s) from %s", imported, json_path)
        return imported

    # -- helpers ---------------------------------------------------------------

    @staticmethod
    def _deactivate_all(db: Session) -> None:
        db.query(ModelRegistrationRecord).filter_by(is_active=True).update(
            {"is_active": False}
        )
