#!/usr/bin/env python3
"""Bootstrap the SQLite validation database for the O1 overlay mount runbook.

Run from the backend/ directory:
    .venv/bin/python tests/live_e2e_test/sandbox/overlay/native/bootstrap_sqlite_validation.py

Creates sqlite:////tmp/eos-validation.db and calls Base.metadata.create_all.

NOTE: The plan's §4.1 step 5 references `task_center_runner.core.models.Base`,
but the actual code uses `db.base.Base` + `import db.models` (the latter is a
load-bearing side effect that populates Base.metadata). This script uses the
correct import path from stores.py:19-20.
"""

from __future__ import annotations

import sys

from sqlalchemy import create_engine

DB_URL = "sqlite:////tmp/eos-validation.db"


def main() -> None:
    # db.models import is load-bearing: it registers all ORM models onto Base.metadata
    from db.base import Base
    import db.models  # noqa: F401

    engine = create_engine(DB_URL, echo=False)
    Base.metadata.create_all(engine)
    engine.dispose()

    tables = sorted(t.name for t in Base.metadata.sorted_tables)
    print(f"created {len(tables)} tables: {tables[:5]} ...")
    assert len(tables) > 0, "No tables created — db.models import may have failed"
    print(f"Database ready at: {DB_URL}")


if __name__ == "__main__":
    main()
    sys.exit(0)
