# NBA Player Props Pipeline (Free Data Only)

## Quickstart

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.nba.txt
python run_pipeline.py --season 2025-26 --force-refresh
```

Output JSON:

- `output/daily_player_props.json`

## Useful flags

- `--require-confirmed-starters` (for teams with confirmed lineup cards, keep only confirmed starters)
- `--no-db` (skip writing to sqlite history store)
- `--force-refresh` (bypass cache)
- `--assist-trap-blitz-weight`, `--assist-hedge-weight`, `--scoring-trap-blitz-weight`
- `--rebound-stretch-big-weight`, `--blowout-minutes-penalty-weight`, `--foul-minutes-penalty-weight`

## Notes

- Pipeline uses only free sources (NBA stats, pbpstats, NBA scoreboard JSON, optional ESPN lineup scrape).
- A sqlite history database is written to `data/nba_props.db` by default.
