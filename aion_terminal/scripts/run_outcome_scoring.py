"""Score matured setup candidates and rebuild the adaptive layer.

This is the feedback loop. Without it ``setup_outcomes``,
``adaptive_expectancy`` and ``setup_performance_stats`` stay empty, the memory
and expectancy channels sit pinned at their 0.5 defaults, and the sizing
modifier has nothing to size from — an agent that cannot be evaluated cannot be
trusted with capital.

The scorer itself (``backtests.outcomes.run_backtest_for_universe``) already
worked; it was simply never invoked by any automated path, only by the manual
``run_backtest`` CLI. This wires it into the scheduled pipeline and then
rebuilds the summaries that the arbiter reads.

Only candidates old enough to have resolved are scored: a candidate written
today has no forward bars to score against, and counting it would silently bias
expectancy toward whatever the last few hours did.

Usage:
    python -m aion_terminal.scripts.run_outcome_scoring
    python -m aion_terminal.scripts.run_outcome_scoring --lookback-days 365   # backfill
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from dotenv import load_dotenv

from aion_terminal.app.config import settings
from aion_terminal.arbitration.adaptive import (
    rebuild_expectancy_summaries,
    rebuild_setup_performance_stats,
)
from aion_terminal.backtests.outcomes import METHOD_SYNTHETIC_GREEKS, run_backtest_for_universe
from aion_terminal.storage.db import bootstrap_schema, get_connection

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "storage" / "schema.sql"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Score matured setups and rebuild the adaptive layer.")
    p.add_argument("--symbols", default="", help="Comma-separated symbols; defaults to the watchlist")
    p.add_argument("--horizon-days", type=int, default=5, help="Bars forward to score a candidate over")
    p.add_argument(
        "--lookback-days",
        type=int,
        default=30,
        help="How far back to draw candidates from. Use a large value to backfill an empty table.",
    )
    p.add_argument("--method", default=METHOD_SYNTHETIC_GREEKS)
    p.add_argument("--all-symbols", action="store_true", help="Score every symbol present in setup_candidates")
    return p.parse_args()


def _table_count(conn, table: str) -> int:
    try:
        return conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    except Exception:
        return -1


def main() -> int:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    args = parse_args()

    conn = get_connection(settings.db_path)
    bootstrap_schema(conn, str(SCHEMA_PATH))

    if args.all_symbols:
        symbols = [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM setup_candidates")]
    else:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or list(settings.watchlist)

    before = {t: _table_count(conn, t) for t in ("setup_outcomes", "adaptive_expectancy", "setup_performance_stats")}

    results = run_backtest_for_universe(
        conn,
        symbols,
        horizon_days=args.horizon_days,
        method=args.method,
        lookback_days=args.lookback_days,
    )

    expectancy_rows = rebuild_expectancy_summaries(conn)
    perf_rows = rebuild_setup_performance_stats(conn)
    conn.commit()

    after = {t: _table_count(conn, t) for t in ("setup_outcomes", "adaptive_expectancy", "setup_performance_stats")}

    print(f"symbols scored      : {len(symbols)}")
    print(f"outcomes produced   : {len(results)}")
    for t in ("setup_outcomes", "adaptive_expectancy", "setup_performance_stats"):
        print(f"{t:<24}: {before[t]} -> {after[t]}")
    print(f"expectancy scopes   : {expectancy_rows}")
    print(f"performance rows    : {perf_rows}")

    if after["setup_outcomes"] == 0:
        # Stated rather than left to be inferred from a zero: the arbiter's
        # memory and expectancy channels stay at their 0.5 defaults and sizing
        # stays None until this table has rows.
        print(
            "\nNo outcomes were scored. Expectancy and memory channels remain at their "
            "defaults and sizing remains unavailable. Likely causes: no candidates "
            "inside --lookback-days, or no forward bars to score them against."
        )

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
