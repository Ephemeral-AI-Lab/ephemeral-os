"""One-shot script to drop pre-rename tier tables (missions/episodes/trials).

Two prior renames left stamps on dev DBs: 2026-05-15 renamed
``missions``/``episodes`` to ``goals``/``iterations``; 2026-05-16 renamed
``trials`` back to ``attempts``. SQLAlchemy's ``create_all`` produces the
new tables but leaves the old ones intact. The startup gate
``db.engine.init_db_with_legacy_check`` refuses to proceed while the old
names exist; run this script once on the affected database to clear them,
then restart.

Usage:
    python -m backend.scripts.drop_legacy_tier_tables --db-url <url>

The script is idempotent — non-existent tables are skipped silently.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import create_engine, inspect, text


# Drop in FK-dependency order: trials → episodes → missions (children before parents).
_LEGACY_TABLES_IN_DROP_ORDER: tuple[str, ...] = ("trials", "episodes", "missions")


def drop_legacy_tier_tables(db_url: str) -> list[str]:
    """Drop the legacy tier tables that exist in the given database.

    Returns the list of tables actually dropped (empty if none were present).
    """
    engine = create_engine(db_url)
    insp = inspect(engine)
    existing = set(insp.get_table_names())
    dropped: list[str] = []
    with engine.begin() as conn:
        for name in _LEGACY_TABLES_IN_DROP_ORDER:
            if name in existing:
                conn.execute(text(f'DROP TABLE IF EXISTS "{name}"'))
                dropped.append(name)
    return dropped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Drop pre-rename tier tables (missions/episodes/trials)."
    )
    parser.add_argument(
        "--db-url",
        required=True,
        help="SQLAlchemy database URL (e.g. sqlite:///./local.db)",
    )
    args = parser.parse_args(argv)
    dropped = drop_legacy_tier_tables(args.db_url)
    print(f"dropped: {dropped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
