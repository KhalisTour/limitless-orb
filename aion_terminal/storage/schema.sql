PRAGMA foreign_keys = ON;

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
