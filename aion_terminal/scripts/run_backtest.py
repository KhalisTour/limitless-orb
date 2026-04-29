from __future__ import annotations

import argparse
import logging
from pathlib import Path

from dotenv import load_dotenv

from aion_terminal.app.config import settings
from aion_terminal.backtests.outcomes import (
    METHOD_ACTUAL_CANDLES,
    METHOD_DELTA_PROXY,
    METHOD_SYNTHETIC_GREEKS,
    query_expectancy_by_method,
    run_backtest_for_universe,
)
from aion_terminal.storage.db import bootstrap_schema, get_connection


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run options expectancy backtests.")
    parser.add_argument("--symbols", default="", help="Comma-separated symbols.")
    parser.add_argument("--horizon-days", type=int, default=5)
    parser.add_argument("--method", default=METHOD_SYNTHETIC_GREEKS, choices=[METHOD_SYNTHETIC_GREEKS, METHOD_DELTA_PROXY, METHOD_ACTUAL_CANDLES])
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def _parse_csv(raw: str) -> list[str]:
    return [token.strip().upper() for token in raw.split(",") if token.strip()]


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)
    load_dotenv()

    symbols = _parse_csv(args.symbols) if args.symbols else [s.upper() for s in settings.watchlist]
    conn = get_connection(settings.db_path)
    schema_path = Path(__file__).resolve().parents[1] / "storage" / "schema.sql"
    bootstrap_schema(conn, str(schema_path))

    results = run_backtest_for_universe(conn, symbols=symbols, horizon_days=args.horizon_days, method=args.method)
    summary = query_expectancy_by_method(conn)
    print(f"Backtest complete: outcomes={len(results)} method={args.method}")
    for row in summary:
        print(row)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
