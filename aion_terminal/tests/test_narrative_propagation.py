"""Tests for sector-to-constituent narrative propagation (P1-2)."""

from __future__ import annotations

from aion_terminal.features.narrative import (
    PROPAGATABLE_TAGS,
    SECTOR_CONSTITUENTS,
    SECTOR_PROPAGATION_WEIGHT,
    propagate_sector_tags,
    sectors_for,
    tag_weight,
)
from aion_terminal.signals.setups import BEARISH_EVENT_TAGS, _narrative_strength


def _tag(symbol, key, **extra):
    return {"symbol": symbol, "tag_key": key, "tag_value": None, **extra}


def test_constituent_inherits_its_sector_tag():
    """The gate on both event_rerating evaluators could never open otherwise."""
    out = propagate_sector_tags("NOW", [_tag("XLK", "sector_rerating_down")])
    assert [t["tag_key"] for t in out] == ["sector_rerating_down"]
    assert out[0]["symbol"] == "NOW"
    assert out[0]["derived_from"] == "XLK"


def test_inherited_tags_are_attenuated():
    out = propagate_sector_tags("NOW", [_tag("XLK", "sector_rerating_down")])
    assert tag_weight(out[0]) == SECTOR_PROPAGATION_WEIGHT
    assert tag_weight(out[0]) < 1.0


def test_direct_tags_keep_full_weight():
    out = propagate_sector_tags("NOW", [_tag("NOW", "bearish_catalyst")])
    assert tag_weight(out[0]) == 1.0


def test_direct_tag_wins_over_the_inherited_duplicate():
    out = propagate_sector_tags(
        "NOW", [_tag("NOW", "sector_rerating_down"), _tag("XLK", "sector_rerating_down")]
    )
    assert len(out) == 1
    assert tag_weight(out[0]) == 1.0


def test_non_constituents_inherit_nothing():
    out = propagate_sector_tags("XOM", [_tag("XLK", "sector_rerating_down")])
    assert out == []


def test_symbol_specific_tags_are_not_propagated():
    """An X-feed mention of the ETF says nothing about a constituent."""
    out = propagate_sector_tags("NOW", [_tag("XLK", "x_feed_mention")])
    assert out == []


def test_broad_market_etfs_have_no_constituents():
    """A tag on SPY propagated to everything would fire the gate universally."""
    for etf in ("SPY", "QQQ", "IWM", "GLD", "IBIT"):
        assert etf not in SECTOR_CONSTITUENTS
        assert propagate_sector_tags("AAPL", [_tag(etf, "sector_rerating_down")]) == []


def test_propagatable_tags_are_sector_scoped():
    assert "x_feed_mention" not in PROPAGATABLE_TAGS
    assert "sector_rerating_down" in PROPAGATABLE_TAGS


def test_a_symbol_can_belong_to_several_sectors():
    assert set(sectors_for("NVDA")) >= {"XLK", "SMH"}


def test_duplicate_inheritance_across_sectors_is_collapsed():
    out = propagate_sector_tags(
        "NVDA", [_tag("XLK", "sector_rerating_down"), _tag("SMH", "sector_rerating_down")]
    )
    assert len(out) == 1


def test_reverse_index_matches_the_forward_map():
    for etf, members in SECTOR_CONSTITUENTS.items():
        for m in members:
            assert etf in sectors_for(m)


# ------------------------- weighting in the evaluators -------------------------


def test_inherited_evidence_scores_below_direct_evidence():
    direct = _narrative_strength([_tag("NOW", "sector_rerating_down")], BEARISH_EVENT_TAGS)
    inherited = _narrative_strength(
        propagate_sector_tags("NOW", [_tag("XLK", "sector_rerating_down")]), BEARISH_EVENT_TAGS
    )
    assert direct == 1.0
    assert 0.0 < inherited < direct


def test_no_matching_tag_scores_zero():
    assert _narrative_strength([_tag("NOW", "bullish_catalyst")], BEARISH_EVENT_TAGS) == 0.0
    assert _narrative_strength([], BEARISH_EVENT_TAGS) == 0.0


def test_strongest_tag_wins():
    """One direct tag is enough for full credit alongside weaker inherited ones."""
    tags = [
        _tag("NOW", "sector_rerating_down", weight=SECTOR_PROPAGATION_WEIGHT),
        _tag("NOW", "bearish_catalyst"),
    ]
    assert _narrative_strength(tags, BEARISH_EVENT_TAGS) == 1.0


def test_malformed_weight_falls_back_to_full():
    assert tag_weight({"weight": "not-a-number"}) == 1.0
    assert tag_weight({}) == 1.0


def test_weight_is_clamped():
    assert tag_weight({"weight": 5.0}) == 1.0
    assert tag_weight({"weight": -2.0}) == 0.0


# --------------------- P3-4: contract symbol extraction ---------------------


def test_backfill_extracts_real_contract_symbols_not_field_names():
    """The loop iterated the bucket's keys, passing "expiry"/"dte_min" as contracts.

    That is why option history 404'd on every call and option_rows was 0
    everywhere — the API was being handed parameter names.
    """
    grouped_chain = {
        "2026-08-07": {
            "expiry": "2026-08-07",
            "dte_min": 2,
            "dte_max": 2,
            "contract_count": 2,
            "contracts": [
                {"option_symbol": "TSLA260807C00400000", "side": "call", "strike": 400.0},
                {"option_symbol": "TSLA260807P00380000", "side": "put", "strike": 380.0},
            ],
        }
    }

    extracted = []
    for bucket in grouped_chain.values():
        for contract in bucket.get("contracts", []):
            sym = contract.get("option_symbol")
            if sym:
                extracted.append(sym)

    assert extracted == ["TSLA260807C00400000", "TSLA260807P00380000"]
    for field_name in ("expiry", "dte_min", "dte_max", "contracts", "contract_count"):
        assert field_name not in extracted
