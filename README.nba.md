# NBA Player Props Pipeline (Free Data Only)

Production-oriented daily Python pipeline for NBA player prop projections and over-line probabilities.

## What it does

- Pulls **free** data from `stats.nba.com`:
  - player box stats
  - player tracking (touches, time of possession, potential assists)
  - team defensive/opponent tendencies
- Pulls free possession/rotation proxies from `pbpstats` endpoints.
- Builds today's slate from NBA's public scoreboard JSON.
- Optionally scrapes ESPN lineups as a starter confirmation enhancement.
- Filters to starter-like players (`GS >= 35` by default).
- Generates projections for points, assists, rebounds, and 3PM.
- Converts projections to probabilities with Poisson + optional Monte Carlo.
- Optionally attaches EV if decimal odds are provided.
- Exports a frontend-friendly JSON payload.

## Quickstart

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run_pipeline.py --season 2025-26 --force-refresh
```

Output is written to:

- `output/daily_player_props.json`

## CLI options

```bash
python run_pipeline.py --help
```

Common flags:

- `--min-games-started 35`
- `--cache-dir cache`
- `--output output/daily_player_props.json`
- `--odds-json odds.json`
- `--no-monte-carlo`
- `--force-refresh` (fetch latest data instead of using cached pulls)
- `--assist-trap-blitz-weight`, `--assist-hedge-weight`, `--scoring-trap-blitz-weight`
- `--rebound-stretch-big-weight`, `--blowout-minutes-penalty-weight`, `--foul-minutes-penalty-weight`

## Optional odds input format

```json
{
  "Nikola Jokic": {
    "points_ge_20": 1.83,
    "assists_ge_6": 1.95,
    "rebounds_ge_8": 1.91,
    "threes_ge_3": 2.7
  }
}
```

## Notes

- NBA Stats endpoints can intermittently reject requests. The pipeline retries and falls back to cached responses when available.
- The model is intentionally modular so you can replace heuristics with trained models while keeping ingestion/export contracts stable.
