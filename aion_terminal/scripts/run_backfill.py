from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta

from dotenv import load_dotenv

from aion_terminal.app.config import settings
from aion_terminal.services.ingestion_service import backfill_option_history, backfill_underlying_bars, refresh_one_symbol

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill historical bars and optional contract history.")
    parser.add_argument("--symbols", default="", help="Comma-separated symbols, e.g. AAPL,SPY")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--timeframes", default="15m,1h,1d")
    parser.add_argument("--contracts-per-symbol", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def _parse_csv(raw: str) -> list[str]:
    return [token.strip().upper() for token in raw.split(",") if token.strip()]


def run_backfill(
    *,
    symbols: list[str] | None = None,
    days: int = 90,
    timeframes: list[str] | None = None,
    contracts_per_symbol: int = 5,
    dry_run: bool = False,
) -> dict[str, dict[str, int | bool | str]]:
    load_dotenv()
    watchlist = symbols or [s.upper() for s in settings.watchlist]
    tfs = timeframes or ["15m", "1h", "1d"]

    end_date = date.today()
    start_date = end_date - timedelta(days=max(1, days))
    start_iso, end_iso = start_date.isoformat(), end_date.isoformat()

    summary: dict[str, dict[str, int | bool | str]] = {}
    for symbol in watchlist:
        status = {"ok": True, "bars": 0, "options": 0, "error": ""}
        try:
            for tf in tfs:
                if dry_run:
                    print(f"[dry-run] {symbol} underlying bars timeframe={tf} start={start_iso} end={end_iso}")
                    continue
                res = backfill_underlying_bars(symbol, tf, start_iso, end_iso)
                status["bars"] += int(res.bars_upserted)
                if not res.ok:
                    status["ok"] = False

            if dry_run:
                print(f"[dry-run] {symbol} option history up to {contracts_per_symbol} contracts")
            else:
                refresh = refresh_one_symbol(symbol)
                contract_symbols = []
                for exp in refresh.grouped_chain.values():
                    for option_symbol in exp.keys():
                        contract_symbols.append(option_symbol)
                contract_symbols = contract_symbols[: max(0, contracts_per_symbol)]
                if not contract_symbols:
                    logger.info("Option candle backfill unavailable/no contracts for %s", symbol)
                for contract in contract_symbols:
                    opt_res = backfill_option_history(contract, start_iso, end_iso)
                    status["options"] += int(opt_res.option_history_rows)
                    if not opt_res.ok:
                        status["ok"] = False
        except Exception as exc:  # pragma: no cover
            status["ok"] = False
            status["error"] = str(exc)
            logger.exception("Backfill failure for %s", symbol)

        summary[symbol] = status

    return summary


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    symbols = _parse_csv(args.symbols) if args.symbols else None
    timeframes = _parse_csv(args.timeframes)

    summary = run_backfill(
        symbols=symbols,
        days=args.days,
        timeframes=timeframes,
        contracts_per_symbol=args.contracts_per_symbol,
        dry_run=args.dry_run,
    )

    total = len(summary)
    ok = sum(1 for s in summary.values() if s.get("ok"))
    print(f"Backfill summary: {ok}/{total} symbols succeeded")
    for symbol, status in summary.items():
        print(
            f"{symbol}: ok={status['ok']} bars={status['bars']} option_rows={status['options']}"
            + (f" error={status['error']}" if status.get("error") else "")
        )
    return 0 if ok == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
