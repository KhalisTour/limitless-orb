from __future__ import annotations

import argparse
from pathlib import Path

from dotenv import load_dotenv

from aion_terminal.app.config import settings
from aion_terminal.scripts.run_backfill import run_backfill
from aion_terminal.storage.db import bootstrap_schema, get_connection

REQUIRED_TABLES = {
    "raw_chain",
    "computed_levels",
    "raw_chain_snapshots",
    "underlying_bars",
    "feature_snapshots",
    "setup_candidates",
    "setup_outcomes",
    "manual_narrative_tags",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bootstrap local Options Terminal history database schema.")
    parser.add_argument("--backfill-days", type=int, default=None, help="Optionally run backfill for N days after schema bootstrap.")
    return parser.parse_args()


def _list_tables(conn) -> list[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    return [str(r[0]) for r in rows]


def main() -> int:
    args = parse_args()
    load_dotenv()

    db_path = Path(settings.db_path)
    if db_path.name == "rs_universe.db":
        raise RuntimeError("Refusing to bootstrap rs_universe.db for history workflow.")

    conn = get_connection(str(db_path))
    try:
        bootstrap_schema(conn, "aion_terminal/storage/schema.sql")
        tables = _list_tables(conn)
        print(f"Database: {db_path}")
        print("Discovered tables:")
        for table in tables:
            print(f" - {table}")

        missing = sorted(REQUIRED_TABLES - set(tables))
        if missing:
            print(f"Missing required tables: {', '.join(missing)}")
            return 1

        print("All required tables present.")
    finally:
        conn.close()

    if args.backfill_days and args.backfill_days > 0:
        print(f"Running backfill for {args.backfill_days} days...")
        run_backfill(days=args.backfill_days)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
