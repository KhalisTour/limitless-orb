# Aion Terminal Operating Manual

## 1) Project Overview

Aion Terminal is a **local FastAPI + SQLite options research terminal** for directional idea discovery and trade planning.

It is designed to help you:
- ingest and cache options/underlying data,
- compute dealer/GEX/OI structure and expiry-aware levels,
- generate technical and volatility features,
- rank setups and score contracts,
- produce disciplined trade plans through agent workflows.

Aion Terminal is **not** automated execution software. It does not place live brokerage orders.

Primary goal: **discover and rank directional options opportunities, then generate disciplined, explainable trade plans from cached research context**.

---

## 2) Core Architecture

Aion Terminal is organized into five layers:

1. **Data refresh layer (CLI scripts)**
   - `bootstrap_history.py`
   - `run_backfill.py`
   - `run_daily_snapshot.py`

2. **Cache/database layer (SQLite)**
   - primary DB: `options_terminal.db`
   - stores chain snapshots, bars, feature snapshots, levels, setups, outcomes

3. **API serving layer (FastAPI)**
   - serves cached data
   - runs in cache-only mode by default

4. **Frontend layer**
   - dashboard + symbol pages + rankings/contracts/setups views + agent pages
   - should consume API responses from SQLite/cache, not trigger broad live refresh

5. **Agent layer**
   - Agent 1: macro brief
   - Agent 2: trade plan generation
   - Agent 3: chart analysis

---

## 3) Data Flow

Typical research flow:

`RS screener / manual watchlist`
→ `run_daily_snapshot`
→ `raw_chain_snapshots / underlying_bars / feature_snapshots`
→ `setup engine / ranking engine / contract scoring`
→ `dashboard/frontend`
→ `Agent 2 trade plan`

Use the scripts to refresh intentionally, then inspect and plan from cached state.

---

## 4) Cache-Only Mode (Default and Recommended)

### Required environment

- `AION_CACHE_ONLY=true`
- `AION_ALLOW_ROUTE_REFRESH=false`
- `AION_MARKETDATA_ENABLED=true`

### Behavior

- FastAPI starts **without** automatic background ingestion.
- Expected startup log:

```text
cache-only mode enabled; background ingestion pipeline disabled
```

- Frontend/API routes read from SQLite/cache.
- Missing data should surface as cache-miss/refresh-required behavior, rather than silently triggering large live fetches.

### Important nuance

`AION_MARKETDATA_ENABLED=true` can remain enabled so CLI scripts can still fetch MarketData.

In cache-only server behavior, route-level live refresh is effectively disabled, so dashboard-like status fields may report effective MarketData route refresh as disabled while scripts still work.

### Why this mode exists

To prevent **429/rate-limit storms** and excess credit burn on MarketData Starter plans.

---

## 5) Environment Variables

Example baseline:

```bash
OPTIONS_DB_PATH=options_terminal.db
MARKETDATA_APP_TOKEN=
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
WATCHLIST=AMD,IONQ,APLD
AION_CACHE_ONLY=true
AION_ALLOW_ROUTE_REFRESH=false
AION_MARKETDATA_ENABLED=true
POLL_SECONDS=600
DTE_MAX=60
```

What each does:

- `OPTIONS_DB_PATH` — SQLite file path for the primary options research database.
- `MARKETDATA_APP_TOKEN` — token for MarketData API ingestion.
- `OPENAI_API_KEY` — key for OpenAI-backed agent calls.
- `ANTHROPIC_API_KEY` — key for Anthropic-backed chart analysis path.
- `WATCHLIST` — default comma-separated symbols used by polling/scripts where applicable.
- `AION_CACHE_ONLY` — when true, disables background ingestion pipeline at server startup.
- `AION_ALLOW_ROUTE_REFRESH` — enables/disables refresh pathways from selected API routes.
- `AION_MARKETDATA_ENABLED` — global MarketData enable flag (commonly true for scripts).
- `POLL_SECONDS` — polling interval when running background polling mode.
- `DTE_MAX` — default max DTE filter used in contract/setups workflows.

---

## 6) Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## 7) Running the Backend Safely

```bash
uvicorn aion_terminal.app.main:app --host 0.0.0.0 --port 8000
```

With `AION_CACHE_ONLY=true`, starting FastAPI should **not** trigger automatic MarketData refresh calls.

---

## 8) Refreshing Market Data (Intended Path)

Use CLI scripts for intentional refresh:

```bash
python -m aion_terminal.scripts.bootstrap_history
```

```bash
python -m aion_terminal.scripts.run_backfill \
  --symbols AMD \
  --days 90 \
  --timeframes 15m,1h,1d \
  --verbose
```

```bash
python -m aion_terminal.scripts.run_daily_snapshot \
  --symbols AMD,IONQ,APLD \
  --max-symbols 3 \
  --sleep-seconds 8 \
  --skip-refresh-if-recent-minutes 240 \
  --verbose
```

Guidelines:
- Use small symbol batches on MarketData Starter plans.
- Avoid casual full-universe refresh runs.
- Scripts are the intended data refresh mechanism.

---

## 9) API Endpoints

### Dashboard
- `GET /dashboard`

### Rankings
- `GET /rankings`
- `GET /rankings/{symbol}`

### Setups / Contracts
- `GET /setups/{symbol}`
- `GET /contracts/{symbol}`
- `GET /expiries/{symbol}`

### Dealer / Snapshot
- `GET /levels/{ticker}`
- `GET /curve/{ticker}`

### RS Screener
- `GET /rs/candidates`
- `GET /rs/candidates/{symbol}`

### Agents
- `GET /agents/brief/latest`
- `POST /agents/brief`
- `POST /agents/chart`
- `GET /agents/chart/history`
- `POST /agents/trade-plan`
- `GET /agents/trade-plan/history`

### Cone
- `GET /cone/etf-bars`

---

## 10) Agents

### Agent 1 — Morning Macro Brief
- Model: `GPT-5.4-mini`
- Optional grader/postprocessor: `GPT-5.4-nano`
- Output: macro regime framing, sector leaders/laggards, narrative tags
- Known issue: quality can depend on web-search/data availability

### Agent 2 — Trade Plan Generator
- Model: `GPT-5.4-mini`
- Prompt system: `TRADE_PLAN_SYSTEM_PROMPT_V3`
- Inputs: rankings, contracts, macro/chart context, `decision_engine` outputs
- Output: narrative + `JSON_PLAN`
- Can emit: `no_trade`, `wait`, `conditional_trade`

### Agent 3 — Chart Analysis
- Primary model: `Claude Haiku 4.5`
- Fallback: `Claude Sonnet 4.6`
- Role: parse chart screenshots + dealer context into structured JSON

---

## 11) Decision Engine

The decision engine is a deterministic quantitative signal layer.

It provides fields such as:
- `S1 / S2 / S3`
- `EV score`
- `p_touch_s1`
- `p_touch_s3`
- `action_bias`

Agent 2 should interpret this as structured signal input and translate it into a human-readable plan.

---

## 12) Backtesting

Current state:
- Synthetic options P&L backtesting exists.
- Delta-proxy method is preserved.
- Synthetic Greeks path estimates delta/gamma/theta/IV effects.
- Historical option candle depth may vary by data plan.

Backtests are research estimates, **not** execution guarantees.

---

## 13) Database Tables

Primary DB: `options_terminal.db`

Key tables:
- `raw_chain`
- `computed_levels`
- `raw_chain_snapshots`
- `underlying_bars`
- `feature_snapshots`
- `setup_candidates`
- `setup_outcomes`
- `manual_narrative_tags`

Do not write to `rs_universe.db` from agent modules unless explicitly intended.

---

## 14) Testing

Run full tests:

```bash
pytest -q
```

Recent startup guard test:

```bash
pytest -q aion_terminal/tests/test_startup_cache_only.py
```

Known non-blocking warnings may include FastAPI `on_event` deprecation and `python_multipart` notices.

---

## 15) Troubleshooting

### Problem: MarketData 429 spam on server start
- Cause: background pipeline or route-refresh leakage triggering MarketData calls.
- Fix: set `AION_CACHE_ONLY=true` and verify startup log shows pipeline disabled.

### Problem: contracts endpoint returns empty
- Cause: cached expiries outside `dte_min/dte_max`, or missing cached chain data.
- Try:

```bash
curl "http://localhost:8000/contracts/AMD?bias=bullish&dte_min=3&dte_max=21"
```

### Problem: `setup_candidates=0`
- Cause: threshold filters, insufficient bars, or missing setup insertion during snapshot flow.

### Problem: only 7–10 bars available
- Fix: run 90-day backfill and verify `underlying_bars` population.

### Problem: macro brief cannot be produced
- Fix: rerun after macro fallback improvements, or accept degraded-output mode if upstream data/search context is limited.

---

## 16) Current Limitations

- MarketData Starter plans have strict credit/rate limits.
- Some symbols may return 404/empty options chains.
- Cached data can become stale between refresh runs.
- Frontend should not auto-trigger broad live refresh.
- Macro output quality depends on web-search reliability.
- Contract scoring needs eligible cached contracts within DTE constraints.
- Not live execution software.

---

## 17) Recommended Daily Workflow

1. Edit `WATCHLIST` or pass symbols manually.
2. Run backfill if bars are insufficient.
3. Run daily snapshot on 1–3 symbols.
4. Start server in cache-only mode.
5. Open dashboard/frontend and inspect rankings/contracts.
6. Generate Agent 2 trade plans only for top candidates.

Concrete example:

```bash
python -m aion_terminal.scripts.run_daily_snapshot \
  --symbols AMD,IONQ,APLD \
  --max-symbols 3 \
  --sleep-seconds 8 \
  --skip-refresh-if-recent-minutes 240 \
  --verbose

uvicorn aion_terminal.app.main:app --host 0.0.0.0 --port 8000
```

Core operating principle:

- **Do not operate as:** open frontend → fetch everything live
- **Operate as:** refresh intentionally → cache data → inspect safely → generate plan

---

## 18) Roadmap

- Improve macro brief quality and consistency.
- Expand historical option-candle integration.
- Strengthen setup candidate persistence and traceability.
- Improve RS → ingestion prioritization.
- Frontend polish and workflow UX improvements.
- Add manual refresh controls with strict safeguards.
- Add trade journal and outcome feedback loops.
