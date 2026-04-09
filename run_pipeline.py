from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from nba_prop_pipeline.config import PipelineConfig
from nba_prop_pipeline.pipeline import run_daily_pipeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the daily NBA player props pipeline.")
    parser.add_argument("--season", default="2025-26", help="Season format YYYY-YY")
    parser.add_argument("--min-games-started", type=int, default=35, help="Starter filter")
    parser.add_argument("--cache-dir", default="cache", help="Cache directory")
    parser.add_argument("--output", default="output/daily_player_props.json", help="Output JSON file")
    parser.add_argument("--odds-json", default=None, help="Path to optional odds JSON for EV")
    parser.add_argument("--no-monte-carlo", action="store_true", help="Disable Monte Carlo probability simulation")
    parser.add_argument("--force-refresh", action="store_true", help="Bypass cache and fetch fresh API responses")
    parser.add_argument("--assist-trap-blitz-weight", type=float, default=0.35, help="Assist scheme weight for trap/blitz deltas")
    parser.add_argument("--assist-hedge-weight", type=float, default=0.18, help="Assist scheme weight for hedge deltas")
    parser.add_argument("--scoring-trap-blitz-weight", type=float, default=-0.12, help="Scoring scheme weight for trap/blitz deltas")
    parser.add_argument("--rebound-stretch-big-weight", type=float, default=0.14, help="Rebound boost weight for stretch-big indicator")
    parser.add_argument("--blowout-minutes-penalty-weight", type=float, default=1.0, help="Multiplier for blowout minutes penalty")
    parser.add_argument("--foul-minutes-penalty-weight", type=float, default=1.0, help="Multiplier for foul-risk minutes penalty")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO))

    odds_map = None
    if args.odds_json:
        odds_map = json.loads(Path(args.odds_json).read_text())

    config = PipelineConfig(
        season=args.season,
        min_games_started=args.min_games_started,
        cache_dir=Path(args.cache_dir),
        output_json=Path(args.output),
        assist_trap_blitz_weight=args.assist_trap_blitz_weight,
        assist_hedge_weight=args.assist_hedge_weight,
        scoring_trap_blitz_weight=args.scoring_trap_blitz_weight,
        rebound_stretch_big_weight=args.rebound_stretch_big_weight,
        blowout_minutes_penalty_weight=args.blowout_minutes_penalty_weight,
        foul_minutes_penalty_weight=args.foul_minutes_penalty_weight,
    )

    output = run_daily_pipeline(
        config=config,
        odds_map=odds_map,
        include_monte_carlo=not args.no_monte_carlo,
        force_refresh=args.force_refresh,
    )
    print(f"Pipeline complete. Wrote {output}")


if __name__ == "__main__":
    main()
