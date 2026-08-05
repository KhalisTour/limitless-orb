PRAGMA foreign_keys = ON;

-- Legacy tables preserved for backward compatibility.
CREATE TABLE IF NOT EXISTS raw_chain (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    option_symbol TEXT NOT NULL,
    strike REAL,
    expiry TEXT,
    type TEXT,
    gamma REAL,
    open_interest INTEGER,
    iv REAL,
    dte INTEGER,
    underlying_price REAL,
    UNIQUE(option_symbol, timestamp)
);

CREATE TABLE IF NOT EXISTS computed_levels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    spot REAL,
    king_node REAL,
    call_wall REAL,
    put_wall REAL,
    regime TEXT
);

CREATE INDEX IF NOT EXISTS idx_raw_chain_symbol_expiry_timestamp
ON raw_chain(symbol, expiry, timestamp);

CREATE INDEX IF NOT EXISTS idx_computed_levels_symbol_timestamp
ON computed_levels(symbol, timestamp);

-- Research-grade normalized tables.
CREATE TABLE IF NOT EXISTS raw_chain_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    expiry TEXT,
    option_symbol TEXT NOT NULL,
    side TEXT,
    strike REAL,
    bid REAL,
    ask REAL,
    last REAL,
    mark REAL,
    iv REAL,
    delta REAL,
    gamma REAL,
    theta REAL,
    vega REAL,
    rho REAL,
    open_interest INTEGER,
    volume INTEGER,
    dte INTEGER,
    underlying_price REAL,
    source TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(option_symbol, snapshot_ts)
);

CREATE TABLE IF NOT EXISTS underlying_bars (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    bar_ts TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume INTEGER,
    vwap REAL,
    source TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(symbol, timeframe, bar_ts)
);

CREATE TABLE IF NOT EXISTS feature_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    expiry TEXT NOT NULL DEFAULT 'combined',
    dte INTEGER,
    spot REAL,
    regime TEXT,
    king_node REAL,
    call_wall REAL,
    put_wall REAL,
    flip_zone REAL,
    features_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(symbol, snapshot_ts, expiry)
);

CREATE TABLE IF NOT EXISTS setup_candidates (
    candidate_id TEXT PRIMARY KEY,
    as_of_ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    setup_class TEXT NOT NULL,
    direction TEXT,
    timeframe TEXT,
    expiry TEXT,
    option_type TEXT,
    strike REAL,
    score REAL NOT NULL,
    rank INTEGER,
    confidence REAL,
    rationale_json TEXT,
    status TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS setup_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT NOT NULL,
    outcome_ts TEXT NOT NULL,
    pnl_abs REAL,
    pnl_pct REAL,
    max_favorable_excursion REAL,
    max_adverse_excursion REAL,
    hold_minutes INTEGER,
    is_winner INTEGER,
    outcome_label TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(candidate_id) REFERENCES setup_candidates(candidate_id)
);

CREATE TABLE IF NOT EXISTS manual_narrative_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    tag_date TEXT NOT NULL,
    tag_key TEXT NOT NULL,
    tag_value TEXT,
    context_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(symbol, tag_date, tag_key)
);

-- Indexes for local query performance.
CREATE INDEX IF NOT EXISTS idx_raw_chain_snapshots_symbol_ts
ON raw_chain_snapshots(symbol, snapshot_ts);

CREATE INDEX IF NOT EXISTS idx_raw_chain_snapshots_expiry
ON raw_chain_snapshots(expiry);

CREATE INDEX IF NOT EXISTS idx_underlying_bars_symbol_ts
ON underlying_bars(symbol, bar_ts);

CREATE INDEX IF NOT EXISTS idx_feature_snapshots_symbol_ts
ON feature_snapshots(symbol, snapshot_ts);

CREATE INDEX IF NOT EXISTS idx_feature_snapshots_expiry
ON feature_snapshots(expiry);

CREATE INDEX IF NOT EXISTS idx_setup_candidates_symbol_asof
ON setup_candidates(symbol, as_of_ts);

CREATE INDEX IF NOT EXISTS idx_setup_candidates_setup_class
ON setup_candidates(setup_class);

CREATE INDEX IF NOT EXISTS idx_setup_candidates_candidate_id
ON setup_candidates(candidate_id);

CREATE INDEX IF NOT EXISTS idx_setup_outcomes_candidate_id
ON setup_outcomes(candidate_id);

CREATE INDEX IF NOT EXISTS idx_setup_outcomes_outcome_ts
ON setup_outcomes(outcome_ts);

CREATE INDEX IF NOT EXISTS idx_manual_tags_symbol_date
ON manual_narrative_tags(symbol, tag_date);

CREATE TABLE IF NOT EXISTS trade_plans (
    plan_id TEXT PRIMARY KEY,
    generated_at TEXT NOT NULL,
    symbol TEXT NOT NULL,
    bias TEXT,
    decision TEXT,
    confidence REAL,
    confidence_label TEXT,
    setup_class TEXT,
    selected_contract_symbol TEXT,
    selected_contract_json TEXT,
    decision_engine_json TEXT,
    json_plan TEXT NOT NULL,
    narrative TEXT NOT NULL,
    context_json TEXT,
    model TEXT,
    tokens_used INTEGER
);

CREATE TABLE IF NOT EXISTS trade_plan_outcomes (
    outcome_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    contract_symbol TEXT,
    entry_ts TEXT,
    entry_price REAL,
    exit_ts TEXT,
    exit_price REAL,
    realized_return_pct REAL,
    mfe_pct REAL,
    mae_pct REAL,
    exit_reason TEXT,
    followed_plan INTEGER,
    notes TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(plan_id) REFERENCES trade_plans(plan_id)
);

CREATE TABLE IF NOT EXISTS agent_memory_summaries (
    memory_id TEXT PRIMARY KEY,
    updated_at TEXT NOT NULL,
    scope TEXT NOT NULL,
    summary_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trade_plans_symbol_generated_at
ON trade_plans(symbol, generated_at);

CREATE INDEX IF NOT EXISTS idx_trade_plan_outcomes_symbol_created_at
ON trade_plan_outcomes(symbol, created_at);

CREATE INDEX IF NOT EXISTS idx_agent_memory_scope
ON agent_memory_summaries(scope, updated_at);

CREATE TABLE IF NOT EXISTS arbitration_snapshots (
    arb_id TEXT PRIMARY KEY,
    generated_at TEXT NOT NULL,
    symbol TEXT NOT NULL,
    arb_decision TEXT NOT NULL,
    final_bias TEXT,
    confidence REAL,
    confidence_bucket TEXT,
    setup_class TEXT,
    agreement_json TEXT NOT NULL,
    conflicts_json TEXT,
    required_trigger_json TEXT,
    kill_switch_json TEXT,
    approved_contract_role TEXT,
    sizing_modifier REAL,
    hold_policy_json TEXT,
    warnings_json TEXT,
    supporting_factors_json TEXT,
    rejection_factors_json TEXT,
    inputs_summary_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS adaptive_expectancy (
    exp_id TEXT PRIMARY KEY,
    updated_at TEXT NOT NULL,
    scope TEXT NOT NULL,
    sample_count INTEGER NOT NULL,
    win_rate REAL,
    avg_pnl_pct REAL,
    avg_mfe_pct REAL,
    avg_mae_pct REAL,
    avg_hold_minutes REAL,
    expectancy_modifier REAL,
    raw_stats_json TEXT,
    UNIQUE(scope)
);

CREATE TABLE IF NOT EXISTS setup_performance_stats (
    stat_id TEXT PRIMARY KEY,
    updated_at TEXT NOT NULL,
    symbol TEXT,
    setup_class TEXT NOT NULL,
    direction TEXT,
    regime TEXT,
    moneyness TEXT,
    dte_bucket TEXT,
    sample_count INTEGER NOT NULL,
    win_rate REAL,
    avg_pnl_pct REAL,
    expectancy_modifier REAL,
    UNIQUE(symbol, setup_class, direction, regime, moneyness, dte_bucket)
);

CREATE INDEX IF NOT EXISTS idx_arbitration_snapshots_symbol_ts
ON arbitration_snapshots(symbol, generated_at);

CREATE INDEX IF NOT EXISTS idx_adaptive_expectancy_scope
ON adaptive_expectancy(scope, updated_at);

CREATE INDEX IF NOT EXISTS idx_setup_performance_symbol_class
ON setup_performance_stats(symbol, setup_class);

CREATE TABLE IF NOT EXISTS morning_briefs (
    brief_id TEXT PRIMARY KEY,
    brief_date TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    regime TEXT,
    dominant_signal TEXT,
    regime_30d_call TEXT,
    risk_level TEXT,
    sector_leaders_json TEXT,
    sector_laggards_json TEXT,
    narrative_tags_json TEXT,
    full_text TEXT,
    exec_summary TEXT,
    model TEXT,
    tokens_used INTEGER,
    raw_json TEXT NOT NULL,
    UNIQUE(brief_date)
);

CREATE INDEX IF NOT EXISTS idx_morning_briefs_date
ON morning_briefs(brief_date DESC);

CREATE TABLE IF NOT EXISTS user_trades (
    trade_id TEXT PRIMARY KEY,
    logged_at TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    contract_symbol TEXT,
    side TEXT,
    strike REAL,
    expiry TEXT,
    dte_at_entry INTEGER,
    entry_price REAL,
    exit_price REAL,
    contracts INTEGER,
    pnl_dollars REAL,
    pnl_pct REAL,
    exit_reason TEXT,
    setup_source TEXT,
    notes TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_user_trades_symbol_date
ON user_trades(symbol, trade_date DESC);

CREATE INDEX IF NOT EXISTS idx_user_trades_date
ON user_trades(trade_date DESC);

-- Chart analyses. Previously held only in an in-memory deque(maxlen=50) inside
-- the API process, so every analysis was lost on restart and none could be
-- audited or compared against what price subsequently did (P2-9).
CREATE TABLE IF NOT EXISTS chart_analyses (
    analysis_id TEXT PRIMARY KEY,
    generated_at TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT,
    source TEXT NOT NULL,            -- 'computed' or the vision model id
    bias TEXT,
    setup_score INTEGER,
    ema_stack TEXT,
    trend TEXT,
    rvol_state TEXT,
    compressed INTEGER,
    setup_class TEXT,
    invalidation_price REAL,
    invalidation_note TEXT,
    brief TEXT,
    warnings_json TEXT,
    dealer_context_json TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chart_analyses_symbol_ts
    ON chart_analyses (symbol, generated_at DESC);
