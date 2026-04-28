# Aion Terminal

## 1. Project Overview

Aion Terminal is a local quantitative options research terminal built on Python,
FastAPI, and SQLite. It ingests real-time options chain data, computes dealer/GEX
structure, scores setups and contracts, screens for relative strength candidates,
and runs AI agents for macro briefing and chart analysis.

This is a research tool. It does not place trades or connect to a brokerage.

## 2. Core Features

- MarketData options chain ingestion (real-time and 15-min delayed)
- Dealer / GEX structure (king node, call wall, put wall, flip zone, GEX curve)
- Expiry-aware levels (front/back expiry structure, OI concentration by expiry)
- Technical feature engine (EMA stack, VWAP, ATR, RVOL, compression, S/R)
- Setup engine (pullback_into_support, momentum_continuation, squeeze_unwind, event_rerating)
- Contract scoring engine (delta/gamma efficiency, liquidity, OI cluster, expected move fit)
- Backtest / outcome mapping (delta proxy expectancy, excursion analysis)
- Rankings API (universe ranking by signal confidence and setup class)
- RS screener (2,385-ticker universe, RS score, RS new high, RS-before-price detection)
- Agent 1: Morning macro brief (Anthropic claude-haiku-4-5, web search enabled)
- Agent 3: Chart analysis (Anthropic claude-sonnet-4-6, vision)

## 3. Architecture

- FastAPI backend serving all research endpoints
- Two SQLite databases (options_terminal.db, rs_universe.db)
- MarketData.app as the options chain data source
- yfinance (raw urllib implementation) for RS screener bar data
- Anthropic API for agent modules
- python-dotenv for environment configuration
- No frontend yet — all interaction via API endpoints and CLI scripts

## 4. Directory Structure

aion_terminal/
  api/           — FastAPI route modules
  agents/        — AI agent modules (brief, chart, prompts)
  backtests/     — Outcome mapping and expectancy engine
  data/          — RS engine, static holdings files, brief outputs
  data_sources/  — MarketData API client
  features/      — Dealer, technical, and contract feature engines
  scripts/       — CLI scripts for ingestion, RS scan, morning brief
  services/      — Ingestion service, ranking service
  signals/       — Setup rules, leadership signal detection
  storage/       — SQLite repositories, schema, db connection
  tests/         — Pytest test suite (70 passing)
  app/           — FastAPI app, config, main entrypoint

## 5. Environment Setup

Python version: 3.12

Commands:
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

Required .env file at project root:

MARKETDATA_APP_TOKEN=your_token_here
OPENAI_API_KEY=sk-your_key_here
ANTHROPIC_API_KEY=sk-ant-your_key_here
OPTIONS_DB_PATH=options_terminal.db
WATCHLIST=NVDA,SPY,QQQ,GLD,COIN,ASTS,PLTR
POLL_SECONDS=3600
DTE_MAX=60

## 6. Running Locally

Start the FastAPI server:

source venv/bin/activate
PYTHONPATH=/path/to/limitless-orb uvicorn aion_terminal.app.main:app --host 0.0.0.0 --port 8000

With auto-reload for development:

PYTHONPATH=/path/to/limitless-orb uvicorn aion_terminal.app.main:app --reload --port 8000

The server starts an ingestion pipeline loop on startup that refreshes all
WATCHLIST symbols at the POLL_SECONDS interval.

## 7. Databases

### options_terminal.db
Primary database. Tables:
- raw_chain — raw options chain records
- computed_levels — dealer structure per symbol per snapshot
- raw_chain_snapshots — timestamped chain snapshots used for scoring
- underlying_bars — OHLCV bars for technical feature computation
- feature_snapshots — computed technical features per symbol
- setup_candidates — setup signals with confidence scores
- setup_outcomes — backtest outcome records
- manual_narrative_tags — narrative event tags per symbol

### aion_terminal/storage/rs_universe.db
RS screener database. Tables:
- rs_candidates — symbols passing the RS screen with scores and expiry
- rs_scan_runs — scan run history and stats

## 8. API Endpoints

### Market Data / Dealer Structure
GET /levels/{symbol}
GET /curve/{symbol}
GET /expiries/{symbol}

### Rankings / Setups / Contracts
GET /rankings
GET /rankings/{symbol}
GET /setups/{symbol}
GET /contracts/{symbol}
POST /tags/manual

### RS Screener
GET /rs/candidates
GET /rs/candidates/{ticker}
GET /rs/scan/status
POST /rs/promote/{ticker}
POST /rs/scan/trigger
GET /rs/events

### Agents
POST /agents/brief
GET /agents/brief/latest
POST /agents/chart
GET /agents/chart/history

### Backtests
GET /backtests/expectancy

## 9. CLI Scripts

### Ingestion
Run a single symbol ingest manually (bypasses the background loop):
PYTHONPATH=. python -m aion_terminal.scripts.run_ingest --symbol NVDA

### RS Screener
Run the full 2,385-ticker RS scan:
PYTHONPATH=. python -m aion_terminal.scripts.run_rs_scan

Force a re-run even if the 2-day interval has not elapsed:
PYTHONPATH=. python -m aion_terminal.scripts.run_rs_scan --force

Compute and display results without writing to the database:
PYTHONPATH=. python -m aion_terminal.scripts.run_rs_scan --dry-run

Combine flags (compute fresh without persisting):
PYTHONPATH=. python -m aion_terminal.scripts.run_rs_scan --force --dry-run

### Morning Brief Agent
Generate and save a morning macro brief:
PYTHONPATH=. python -m aion_terminal.scripts.run_morning_brief

Generate without posting narrative tags to the database:
PYTHONPATH=. python -m aion_terminal.scripts.run_morning_brief --no-tags

Validate configuration and prompt without making an API call:
PYTHONPATH=. python -m aion_terminal.scripts.run_morning_brief --dry-run

## 10. RS Screener

The RS screener scans a combined universe of 2,385 unique tickers drawn from:
- IWM holdings (1,884 US equities, aion_terminal/data/iwm_holdings.txt)
- S&P 500 holdings (503 US equities, aion_terminal/data/sp500_holdings.txt)

Bar data is fetched via direct Yahoo Finance HTTP requests (no yfinance binary dependency).
SPY is used as the benchmark.

A ticker passes the screen when 3 of 5 conditions are met:
1. RS daily rating in top 25% of universe (percentile >= 75)
2. RS new high over 63-day lookback
3. RS new high before price new high (early leadership signal)
4. Price above 200-day moving average
5. Price above 50-week moving average (approximated as 250-day MA)

Passing candidates are stored in rs_universe.db with a 21-day TTL.
Promoted tickers can be added to the active watchlist for chain ingestion.

The scan runs every 2 days. Use --force to override the interval.
A full scan of 2,385 tickers takes approximately 8-10 minutes.

## 11. Agent System

### Agent 1 — Morning Macro Brief
Model: gpt-5.4-mini
Provider: OpenAI
Purpose: generates a 900-1,300 word institutional macro regime brief covering
  10 sections: regime, liquidity, rates, growth, inflation, credit, geopolitics,
  sectors, cross-asset, risk matrix. Ends with trading implications per ticker.
Output: saves JSON to aion_terminal/data/briefs/{date}_morning_brief.json
  and posts narrative_tags to options_terminal.db

### Agent 3 — Chart Analysis
Model: claude-haiku-4-5 (fallback: claude-sonnet-4-6)
Provider: Anthropic
Input: chart screenshot (PNG or JPEG) + optional dealer context from /rankings/{symbol}
Output: JSON with setup_score (0-5), bias, EMA stack, RVOL state, RSI divergence,
  setup_class, invalidation_note, and 2-3 sentence brief

### Agent 2 — Trade Plan Generator (planned)
Status: system prompt designed, Codex build prompt not yet submitted
Purpose: combines terminal rankings, contract scores, macro tags, chart analysis,
  and session context to generate structured trade plans with three-level
  structural exits tied to dealer levels

## 12. Testing

Run the full test suite:
pytest -q

Run with verbose output:
pytest aion_terminal/tests/ -v

Current status: 70 tests passing across:
- test_agents.py (9 tests)
- test_backtest_mapping.py (6 tests)
- test_contract_scoring.py (7 tests)
- test_dealer_expiry_levels.py (1 test)
- test_dealer_features.py (1 test)
- test_ingestion_service.py (1 test)
- test_marketdata_expiry_selection.py (1 test)
- test_ranking_service.py (5 tests)
- test_rs_engine.py (14 tests)
- test_setup_rules.py (10 tests)
- test_storage_repositories.py (3 tests)
- test_technical_features.py (8 tests)

No live API calls are made in tests. All external dependencies are monkeypatched.

## 13. Current Limitations

- MarketData Starter plan ($29/mo) provides 15-min delayed options data.
  Real-time OPRA requires the Trader plan.
- Daily credit limit of ~10,000 API credits. Full universe refresh costs ~112 credits.
- yfinance bar data for the RS screener may be delayed or unavailable for
  thinly traded tickers.
- The ANTHROPIC_API_KEY must be set in .env for agent modules to function.
- Chart agent requires a clear, labeled TradingView or equivalent screenshot.
  It cannot interpret unlabeled or low-resolution charts reliably.
- No frontend exists yet. All interaction is via API endpoints and CLI scripts.
- This is not execution software. It does not connect to any brokerage.
- Agent 2 (trade plan generator) is designed but not yet built.
- RS scan takes 8-10 minutes for the full universe due to rate limiting.

## 14. Development Workflow

Branch: codex-prompt3

Standard workflow:
git checkout codex-prompt3
pytest -q
# make changes
git add .
git commit -m "feat: description"
git push origin codex-prompt3

Codex task workflow:
1. Create new Codex task pointing at codex-prompt3
2. Review PR and run: git diff --name-only codex-prompt3 origin/<codex-branch>
3. Verify only new files appear in the diff
4. Cherry-pick new files: git checkout origin/<codex-branch> -- path/to/file
5. Run pytest -q to confirm all tests pass
6. Commit and push to codex-prompt3

## 15. Roadmap

In approximate priority order:
- Agent 2: trade plan generator with S1/S2/S3 state framework, structural exits,
  session context injection, and outcome feedback loop
- Prompt 10: single-file HTML frontend with dealer structure visualization,
  RS screener watchlist, trade plan builder, structural trailing stop display,
  and session log
- Macro brief evaluator: lightweight grading loop using claude-haiku-4-5
  to score brief quality after generation
- macro_style_guide.md RAG injection: pass compact exemplars and style rules
  into each brief call
- Agent 4: narrative tag collector from X timeline / RSS feeds
- Unusual Whales API integration: flow alerts, sector tide, Greek exposure
- Weekly pattern summary: Sunday evening script querying setup_outcomes
  to generate per-user historical performance by setup class, DTE, and ticker
- Frontend upgrade: React or HTML artifact with live dealer curve,
  RS screener integration, and agent chat panel
- Databento / tick data integration (longer term)
