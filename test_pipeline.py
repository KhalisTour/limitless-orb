import sqlite3
from aion_terminal.storage.db import get_connection, bootstrap_schema
from aion_terminal.storage.repositories import query_latest_chain
from aion_terminal.features.dealer import compute_levels
from aion_terminal.features.technical import build_technical_features, TechnicalFeatures, TechnicalState
from aion_terminal.signals.setups import evaluate_symbol_snapshot
from aion_terminal.models.dto import UnderlyingBarRecord

SYMBOL = "SPY"

conn = get_connection("options_terminal.db")
bootstrap_schema(conn, "aion_terminal/storage/schema.sql")

# Pull bars from DB
rows = conn.execute(
    "SELECT * FROM underlying_bars WHERE symbol=? ORDER BY bar_ts DESC LIMIT 60",
    (SYMBOL,)
).fetchall()
bars = [UnderlyingBarRecord(
    symbol=row["symbol"],
    timeframe=row["timeframe"],
    bar_ts=row["bar_ts"],
    open=row["open"],
    high=row["high"],
    low=row["low"],
    close=row["close"],
    volume=row["volume"],
    vwap=row["vwap"],
) for row in reversed(rows)]

print(f"Bars loaded: {len(bars)}")

# Pull chain from DB
chain = query_latest_chain(conn, SYMBOL)
spot = chain[0]["underlying_price"] if chain else 0.0
dealer = compute_levels(chain, spot, symbol=SYMBOL)

print(f"Spot: {spot}")
print(f"King node: {dealer['king_node']}")
print(f"Regime: {dealer['regime']}")

# Run technical engine
tech_features, tech_state = build_technical_features(bars, "D")
print(f"EMA stack: {tech_state.ema_stack}")
print(f"Trend: {tech_state.trend}")
print(f"Above VWAP: {tech_state.above_vwap}")
print(f"Compressed: {tech_state.compressed}")
print(f"High RVOL: {tech_state.high_rvol}")

# Run setup engine
signals = evaluate_symbol_snapshot(
    symbol=SYMBOL,
    dealer_features=dealer,
    technical_features=tech_features,
    technical_state=tech_state,
    narrative_tags=[],
)

print(f"\nSetup signals: {len(signals)}")
for s in signals:
    print(f"  {s.setup_class} | {s.bias} | confidence={s.confidence_raw:.2f} | invalidation={s.invalidation_rule}")

conn.close()
