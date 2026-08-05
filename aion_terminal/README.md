# AION Terminal

## 1. Project Overview

AION Terminal is a research terminal for options decision support. It is explicitly
**not** an execution platform — it does not route orders, manage positions, or
broker-integrate. Its purpose is to support two narrow decision surfaces:

- **Sizing decisions:** how large (if at all) a discretionary trade should be,
  given dealer positioning, relative strength, regime, and backtested
  expectancy for the candidate setup class.
- **Structural exits:** where the position fails based on dealer levels
  (king node, call/put walls, flip zone) rather than arbitrary stop distances.

The terminal aggregates option chain snapshots, underlying bars, narrative
context, and LLM-generated trade plans into a normalized local SQLite database.
All decisions remain discretionary; the agents and arbitration layer only
produce ranked candidates and structured rationale.

## 2. Core Loop

The end-to-end research loop runs in four stages:

1. **Snapshot** — `services/ingestion_service` fetches option chains and
   underlying bars from MarketData.app and writes normalized rows into
   `raw_chain_snapshots` and `underlying_bars`.
2. **Ranking** — `services/ranking_service` and `signals/setups` compute
   dealer levels, regime, and candidate setups, persisting to
   `feature_snapshots` and `setup_candidates`.
3. **Arbitration** — `arbitration/` reconciles disagreement between signal
   sources (technical setup, dealer positioning, relative strength,
   adaptive expectancy) and writes an `arbitration_snapshots` row with a
   final bias, confidence, sizing modifier, and kill-switch flags.
4. **Trade Plan Agent** — `agents/trade_plan_agent` consumes the arbitration
   snapshot, ranked contracts, and memory summaries to produce a structured
   trade plan stored in `trade_plans` (JSON plan + narrative + selected
   contract).

## 3. Daily Workflow

Typical operator day:

```bash
# Morning (runs via cron at 06:30 ET on weekdays — see Section 9)
./aion_terminal/scripts/run_morning_refresh.sh

# Verify schema, freshness, env, agent connectivity
python -m aion_terminal.scripts.run_diagnostics

# Start the API + UI
uvicorn aion_terminal.app.main:app --host 0.0.0.0 --port 8000

# Intraday refresh (manual, on demand)
python -m aion_terminal.scripts.run_daily_snapshot
```

The morning shell script sequences: backfill bars → snapshot → cache invalidate
→ morning brief, and on Fridays writes a weekly JSON rollup.

## 4. Environment Variables

Loaded via `python-dotenv` from `.env` and consumed in `aion_terminal/app/config.py`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `OPTIONS_DB_PATH` | `options_terminal.db` | SQLite file path for the main DB |
| `MARKETDATA_APP_TOKEN` | _(empty)_ | MarketData.app API token for chains and bars |
| `OPENAI_API_KEY` | _(empty)_ | OpenAI key for brief, chart, trade plan, chat agents |
| `ANTHROPIC_API_KEY` | _(empty)_ | Optional secondary LLM provider key |
| `WATCHLIST` | `SPY,QQQ,AAPL` | Comma-separated universe of symbols |
| `POLL_SECONDS` | `600` | Background pipeline poll interval (seconds) |
| `DTE_MAX` | `60` | Maximum days-to-expiry considered. Threaded through `ranking_service.DEFAULT_DTE_MAX`; previously the ranking layer used a hardcoded `21` regardless of this setting. |
| `AION_CACHE_ONLY` | `true` | If true, disables background ingestion (read DB only) |
| `AION_ALLOW_ROUTE_REFRESH` | `false` | If true, ranking routes may trigger live refresh |
| `AION_MARKETDATA_ENABLED` | `true` | Master kill switch for MarketData calls |

No `.env.example` file is shipped; copy the variables above into a local `.env`.

## 5. API Endpoints

All endpoints registered on the FastAPI app in `aion_terminal/app/main.py`.

### Dashboard
- `GET /dashboard` — Aggregated dashboard payload.

### Snapshot
- `GET /levels/{ticker}` — Latest dealer levels for a symbol.
- `GET /curve/{ticker}` — Gamma/OI curve, optional `expiry` filter.
- `GET /expiries/{ticker}` — Available expiries cached for a symbol.
- `WS  /ws/{ticker}` — Streaming levels websocket.

### Universe
- `GET /universe` — Current watchlist universe.

### Rankings & Setups
- `GET /rankings/health` — Ranking subsystem health.
- `GET /rankings` — Ranked candidates across the universe.
- `GET /rankings/{symbol}` — Per-symbol ranking detail.
- `GET /setups/{symbol}` — Current setup candidates for a symbol.
- `GET /contracts/{symbol}` — Ranked option contracts for a symbol.
- `POST /tags/manual` — Attach a manual narrative tag.

### Contracts
- `GET /contracts/health` — Contracts subsystem health.
- `GET /contracts/universe` — Universe of recently scored contracts.

### Arbitration
- `GET /candidates` — Latest arbitration candidates list.
- `POST /rebuild` — Rebuild adaptive expectancy and arbitration in background.
- `GET /{symbol}` — Arbitration snapshot for one symbol.

### Agents
- `POST /agents/brief` — Generate a morning brief.
- `GET /agents/brief/history` — Brief history (default 14 days).
- `GET /agents/brief/latest` — Most recent brief.
- `POST /agents/chart` — Chart analysis from an uploaded image (vision model).
- `POST /agents/chart/computed` — Chart read derived from `underlying_bars`. No image, no model call, no API key; deterministic and reproducible.
- `GET /agents/chart/history` — Chart analysis history.
- `POST /agents/trade-plan` — Generate a trade plan for a symbol.
- `GET /agents/trade-plan/history` — Trade plan history.
- `POST /agents/trade-plan/{plan_id}/outcome` — Log outcome for a plan.
- `GET /agents/trade-plan/outcomes` — Trade plan outcomes list.
- `GET /agents/memory/summary` — Get agent memory summary by scope.
- `POST /agents/memory/rebuild` — Recompute memory summary from outcomes.
- `POST /agents/chat` — Conversational agent endpoint.

### Trades (user logging)
- `POST /trades/log` — Log a discretionary user trade.
- `GET /trades/history` — User trade history.
- `GET /trades/summary` — Aggregated user trade statistics.

### Backtests
- `GET /backtests/health` — Backtest subsystem health.
- `GET /backtests/expectancy` — Stored expectancy by setup class.

### Cone / Bars
- `GET /cone/etf-bars` — ETF bar data for cone visualization.

### Tags
- `GET /tags/health` — Tags subsystem health.
- `GET /tags/{symbol}` — Narrative tags for a symbol.

### Relative Strength
- `GET /rs/candidates` — Current RS candidate list.
- `GET /rs/candidates/{ticker}` — Per-ticker RS detail.
- `GET /rs/scan/status` — Last RS scan run status.
- `POST /rs/promote/{ticker}` — Promote an RS candidate into the watchlist.
- `POST /rs/scan/trigger` — Trigger an RS scan in background.
- `GET /rs/history` — Recent RS scan history.
- `GET /rs/events` — Leadership events above a percentile threshold.

### Nitter
- `GET /feed/x` — Pull X/Twitter feed via Nitter and persist tags.

### Health & Cache
- `POST /cache/invalidate` — Invalidate ranking cache.
- `GET /health/data-freshness` — Per-source freshness status.

## 6. Database Tables

Defined in `aion_terminal/storage/schema.sql`:

| Table | Purpose |
| --- | --- |
| `raw_chain` | Legacy flat option chain log retained for backward compatibility. |
| `computed_levels` | Legacy dealer levels (king node, walls, regime) per snapshot. |
| `raw_chain_snapshots` | Normalized per-contract chain rows with greeks and prices. |
| `underlying_bars` | OHLCV bars per symbol and timeframe. |
| `feature_snapshots` | Computed levels and feature JSON per symbol/expiry/timestamp. |
| `setup_candidates` | Scored setup candidates produced by the signals layer. |
| `setup_outcomes` | Realized outcomes (PnL, MFE, MAE, hold) keyed to candidates. |
| `manual_narrative_tags` | Operator-applied narrative tags by symbol/date. |
| `trade_plans` | LLM-generated structured trade plans with selected contract. |
| `trade_plan_outcomes` | Realized outcomes logged against trade plans. |
| `agent_memory_summaries` | Rolled-up agent memory keyed by scope. |
| `arbitration_snapshots` | Final arbitration decision per symbol with confidence and sizing. |
| `chart_analyses` | Persisted chart reads (computed or vision), previously in-memory only. |
| `adaptive_expectancy` | Rolling expectancy stats per scope used as sizing modifier. |
| `setup_performance_stats` | Win-rate and expectancy aggregated by setup class slice. |
| `morning_briefs` | Saved morning brief output with exec summary and full text. |
| `user_trades` | Discretionary trades logged by the operator. |

## 7. Scripts Reference

Located in `aion_terminal/scripts/`:

- `bootstrap_history.py` — Bootstrap local schema and optionally run an initial backfill.
- `run_backfill.py` — Backfill historical bars and optional contract history for a symbol list.
- `run_backtest.py` — Run options expectancy backtests across the universe using a chosen method.
- `run_daily_snapshot.py` — End-of-loop snapshot: refresh, feature compute, setup scoring, persist.
- `run_arbitration.py` — Run arbitration across the watchlist headlessly and persist results.
- `run_diagnostics.py` — Pre-flight checks: DB connectivity, schema, freshness, agents, env, server, plus semantic checks that assert the system is producing usable output.
- `run_instrumentation.py` — Report setup production, decision distribution, agreement-channel liveness and dealer-map coherence.
- `run_outcome_scoring.py` — Score matured candidates and rebuild the adaptive expectancy layer.
- `run_morning_brief.py` — Generate the morning brief and write weekly JSON rollups on Fridays.
- `run_morning_refresh.sh` — Shell sequence: backfill → snapshot → cache invalidate → brief.
- `run_rs_scan.py` — Run the relative strength screener scan and log leadership events.

## 8. Known Limitations

- **MarketData Starter plan** provides limited historical bar depth; long
  backfills may return truncated history.
- **OpenAI TPM throttling** can slow or fail brief, chart, and trade plan
  generation under load; agents do not currently retry with backoff.
- **Minimum 55 bars** are required before the ranking pipeline will score a
  symbol; newly added tickers are skipped until enough history accumulates.
- **Nitter instance availability** varies; the `/feed/x` route depends on
  whichever public instance is responsive at request time.
- **Up to 6 expiries within 14 days** are fetched per symbol per snapshot
  cycle to keep API usage bounded.

## 9. Cron Setup

Install the morning refresh on weekdays at 06:30 ET. Edit your crontab:

```bash
crontab -e
```

Add (adjust path to your repo and confirm host timezone or use `CRON_TZ`):

```
CRON_TZ=America/New_York
30 6 * * 1-5 /Users/khaliwilliams/limitless-orb/aion_terminal/scripts/run_morning_refresh.sh
```

Logs are written to `logs/morning_YYYY-MM-DD.log`.
