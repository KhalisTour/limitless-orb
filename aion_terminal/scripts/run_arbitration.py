"""Run arbitration across the watchlist and persist the results.

``run_daily_snapshot`` writes features, setups, and contracts but never
invokes the arbiter — the only paths that did were the FastAPI routes, so a
headless pipeline run produced no ``arbitration_snapshots`` rows at all.
This script closes that gap and makes the reasoning layer measurable
without standing up the server.

Unlike ``get_arbitration_candidates``, failures here are reported rather
than swallowed: a symbol that cannot be arbitrated is a finding, not a
silent omission.

Usage:
    python -m aion_terminal.scripts.run_arbitration
    python -m aion_terminal.scripts.run_arbitration --symbols TSLA,AAPL
"""

from __future__ import annotations

import argparse
import logging
import traceback
from pathlib import Path

from dotenv import load_dotenv

from aion_terminal.app.config import settings
from aion_terminal.arbitration.service import get_arbitration
from aion_terminal.storage.db import bootstrap_schema, get_connection

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run arbitration for watchlist symbols.")
    parser.add_argument("--symbols", default="", help="Comma-separated symbols; defaults to the watchlist")
    parser.add_argument("--max-symbols", type=int, default=None, help="Cap the number of symbols processed")
    parser.add_argument("--verbose", action="store_true", help="Print full tracebacks for failures")
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    args = parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or list(settings.watchlist)
    if args.max_symbols is not None:
        symbols = symbols[: args.max_symbols]

    # Resolved from the package rather than the cwd so the script works from
    # any working directory.
    schema_path = Path(__file__).resolve().parent.parent / "storage" / "schema.sql"
    conn = get_connection(settings.db_path)
    bootstrap_schema(conn, str(schema_path))

    ok = 0
    failed: list[tuple[str, str]] = []
    for sym in symbols:
        try:
            arb = get_arbitration(sym, conn)
        except Exception as exc:  # surfaced, not swallowed
            failed.append((sym, f"{type(exc).__name__}: {exc}"))
            if args.verbose:
                traceback.print_exc()
            print(f"{sym:<6} | FAILED | {type(exc).__name__}: {exc}")
            continue

        ok += 1
        m = arb.agreement_matrix
        print(
            f"{sym:<6} | {arb.arb_decision:<16} | bias={arb.final_bias:<8} "
            f"conf={arb.confidence:.3f} | avg={m.average():.3f} "
            f"| tech={m.technical:.2f} dealer={m.dealer:.2f} con={m.contracts:.2f} "
            f"macro={m.macro:.2f} mem={m.memory:.2f} exp={m.expectancy:.2f}"
        )

    print(f"\narbitrated {ok}/{len(symbols)} symbols")
    if failed:
        print(f"failures ({len(failed)}):")
        for sym, err in failed:
            print(f"  {sym}: {err}")

    conn.close()


if __name__ == "__main__":
    main()
