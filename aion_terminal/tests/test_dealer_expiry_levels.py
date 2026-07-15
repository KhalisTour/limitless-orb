from __future__ import annotations

import sys
import types

if "dotenv" not in sys.modules:
    sys.modules["dotenv"] = types.SimpleNamespace(load_dotenv=lambda *a, **k: None)

import pytest

from aion_terminal.arbitration import service as arb_service
from aion_terminal.features.dealer import compute_combined_and_expiry_levels, compute_levels
from aion_terminal.models.dto import FeatureSnapshotRecord
from aion_terminal.storage.db import bootstrap_schema, get_connection
from aion_terminal.storage.repositories import insert_feature_snapshots, query_latest_chain
from aion_terminal.utils.time_utils import utc_now_iso


SCHEMA_PATH = "aion_terminal/storage/schema.sql"

# (expiry, option_symbol, side, strike, gamma, open_interest)
_CHAIN = [
    ("2026-08-01", "T_A_C110", "call", 110.0, 0.30, 200),
    ("2026-08-01", "T_A_C105", "call", 105.0, 0.10, 100),
    ("2026-08-01", "T_A_P90", "put", 90.0, 0.20, 300),
    ("2026-08-08", "T_B_C108", "call", 108.0, 0.25, 150),
    ("2026-08-08", "T_B_P92", "put", 92.0, 0.15, 250),
]
_SPOT = 100.0
_SYMBOL = "TESTX"


@pytest.fixture()
def conn(tmp_path):
    c = get_connection(str(tmp_path / "levels.db"))
    bootstrap_schema(c, SCHEMA_PATH)
    yield c
    c.close()


def _insert_raw_chain(conn, snapshot_ts: str) -> None:
    now = utc_now_iso()
    for expiry, opt, side, strike, gamma, oi in _CHAIN:
        conn.execute(
            """INSERT INTO raw_chain_snapshots
               (snapshot_ts, symbol, expiry, option_symbol, side, strike, gamma,
                open_interest, dte, underlying_price, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (snapshot_ts, _SYMBOL, expiry, opt, side, strike, gamma, oi, 5, _SPOT, now, now),
        )
    conn.commit()


def _grouped_from_chain() -> dict:
    grouped: dict[str, dict] = {}
    for expiry, opt, side, strike, gamma, oi in _CHAIN:
        grouped.setdefault(expiry, {"contracts": []})["contracts"].append(
            {"option_symbol": opt, "side": side, "strike": strike, "gamma": gamma, "open_interest": oi}
        )
    return grouped


def _persist_feature_snapshots(conn, snapshot_ts: str, combined: dict, expiry_levels: dict) -> None:
    records = [
        FeatureSnapshotRecord(
            snapshot_ts=snapshot_ts, symbol=_SYMBOL, expiry="combined", dte=None,
            spot=combined["spot"], regime=combined["regime"], king_node=combined["king_node"],
            call_wall=combined["call_wall"], put_wall=combined["put_wall"], flip_zone=combined["flip_zone"],
        )
    ]
    for expiry, level in expiry_levels.items():
        records.append(
            FeatureSnapshotRecord(
                snapshot_ts=snapshot_ts, symbol=_SYMBOL, expiry=expiry, dte=5,
                spot=level["spot"], regime=level["regime"], king_node=level["king_node"],
                call_wall=level["call_wall"], put_wall=level["put_wall"], flip_zone=level["flip_zone"],
            )
        )
    insert_feature_snapshots(conn, records)


def test_compute_combined_and_expiry_levels_produces_expiry_maps():
    grouped_chain = {
        "2026-04-10": {
            "contracts": [
                {"option_symbol": "OPT1", "side": "call", "strike": 100, "gamma": 0.2, "open_interest": 100, "iv": 0.2, "dte": 1},
                {"option_symbol": "OPT2", "side": "put", "strike": 95, "gamma": 0.1, "open_interest": 200, "iv": 0.3, "dte": 1},
            ]
        },
        "2026-04-17": {
            "contracts": [
                {"option_symbol": "OPT3", "side": "call", "strike": 105, "gamma": 0.3, "open_interest": 150, "iv": 0.2, "dte": 8}
            ]
        },
    }

    combined, expiry_levels = compute_combined_and_expiry_levels(grouped_chain, spot=100.0, symbol="AAPL")

    assert "2026-04-10" in expiry_levels
    assert "2026-04-17" in expiry_levels
    assert isinstance(combined.get("curve"), list)
    assert "king_node" in expiry_levels["2026-04-10"]
    assert "regime" in expiry_levels["2026-04-17"]


def test_combined_map_matches_flat_ranking_compute():
    """P0-1: the persisted combined map (compute_combined_and_expiry_levels) must
    equal the flat map the ranking service computes (compute_levels) for the same
    contracts. Same source-of-truth math, so the two paths cannot diverge."""
    grouped = _grouped_from_chain()
    combined, _ = compute_combined_and_expiry_levels(grouped, spot=_SPOT, symbol=_SYMBOL)
    flat_rows = [
        {"symbol": _SYMBOL, "type": side, "strike": strike, "gamma": gamma, "open_interest": oi}
        for _e, _o, side, strike, gamma, oi in _CHAIN
    ]
    ranking_levels = compute_levels(flat_rows, spot=_SPOT, symbol=_SYMBOL)
    for key in ("king_node", "call_wall", "put_wall", "flip_zone", "regime"):
        assert combined[key] == ranking_levels[key], key


def test_arbiter_scores_same_dealer_levels_as_ranking(conn):
    """P0-1 regression: for a given (symbol, snapshot_ts) the dealer levels the
    arbiter reads (service._load_ranking) must be byte-identical to the ones the
    ranking service computes via compute_levels on the same chain."""
    snapshot_ts = utc_now_iso()
    _insert_raw_chain(conn, snapshot_ts)

    # Ranking-service path: flat, combined-across-expiries.
    flat_rows = query_latest_chain(conn, _SYMBOL)
    ranking_levels = compute_levels(flat_rows, spot=_SPOT, symbol=_SYMBOL)

    # Writer path: persist combined + per-expiry snapshots under one snapshot_ts.
    combined, expiry_levels = compute_combined_and_expiry_levels(
        _grouped_from_chain(), spot=_SPOT, symbol=_SYMBOL
    )
    _persist_feature_snapshots(conn, snapshot_ts, combined, expiry_levels)

    # Arbiter path.
    arb_ranking = arb_service._load_ranking(conn, _SYMBOL)
    dealer = arb_ranking["dealer_structure"]

    assert dealer["king_node"] == ranking_levels["king_node"]
    assert dealer["call_wall"] == ranking_levels["call_wall"]
    assert dealer["put_wall"] == ranking_levels["put_wall"]
    assert arb_ranking["regime"] == ranking_levels["regime"]


def test_load_ranking_selects_combined_not_arbitrary_expiry(conn):
    """P0-1 regression: _load_ranking must select the combined row explicitly,
    even when a per-expiry row is the most recent by snapshot_ts."""
    snapshot_ts = utc_now_iso()
    combined, expiry_levels = compute_combined_and_expiry_levels(
        _grouped_from_chain(), spot=_SPOT, symbol=_SYMBOL
    )
    _persist_feature_snapshots(conn, snapshot_ts, combined, expiry_levels)

    # A per-expiry row written *later* with a deliberately divergent call wall.
    later_ts = utc_now_iso() + "-later"
    insert_feature_snapshots(
        conn,
        [
            FeatureSnapshotRecord(
                snapshot_ts=later_ts, symbol=_SYMBOL, expiry="2026-08-01", dte=5,
                spot=_SPOT, regime="range", king_node=999.0, call_wall=999.0, put_wall=1.0, flip_zone=None,
            )
        ],
    )

    arb_ranking = arb_service._load_ranking(conn, _SYMBOL)
    assert arb_ranking["dealer_structure"]["call_wall"] == combined["call_wall"]
    assert arb_ranking["dealer_structure"]["call_wall"] != 999.0
