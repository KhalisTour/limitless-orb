# Aion Terminal (Phase 1 Refactor)

## Run locally

```bash
export MARKETDATA_APP_TOKEN=your_token
python -m uvicorn aion_terminal.app.main:app --host 0.0.0.0 --port 8000
```

## Migration path from legacy `terminal.py`

1. Keep using the same SQLite file (`options_terminal.db`) via `OPTIONS_DB_PATH`.
2. On startup, `bootstrap_schema` executes `storage/schema.sql` idempotently.
3. Existing `raw_chain` and `computed_levels` are preserved since schema uses `CREATE TABLE IF NOT EXISTS`.
4. Existing API contracts are preserved for:
   - `GET /levels/{ticker}`
   - `GET /curve/{ticker}`
   - `GET /expiries/{ticker}`
   - `WS /ws/{ticker}`
5. If migrating from a custom DB path, set `OPTIONS_DB_PATH=/path/to/legacy.db` before startup.

## Notes

- SQL is centralized in `storage/repositories.py` and `storage/schema.sql`.
- Dealer-level logic is in `features/dealer.py`.
- Ingestion and polling loop are in `services/`.
