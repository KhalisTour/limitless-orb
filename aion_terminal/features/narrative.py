"""Sector-to-constituent narrative propagation.

The only writer of ``manual_narrative_tags`` is the morning brief, and it tags
ETFs — ``SPY``, ``XLK``, ``XLE``, ``GLD`` — never a watchlist single name.
Measured across the historical tags: ``sector_rerating_down`` appears 20 times
and ``sector_rerating_up`` 15, all on ETFs. Both ``event_rerating`` evaluators
gate on a narrative tag being present for the symbol under evaluation, so that
gate could never open for a single name and the strongest component in the
system — the macro brief — was disconnected from the single-name layer.

This module bridges that gap: a sector ETF's tag propagates to its
constituents, attenuated. Attenuation matters. "XLK is rerating down" is real
evidence about NVDA, but it is weaker than a tag placed on NVDA directly, and
scoring the two identically would let a single sector call light up every name
it touches at full strength.

Broad-market ETFs are deliberately excluded: a tag on SPY propagated to
everything says nothing about any particular name, and would fire the narrative
gate universally — which is the same defect in the opposite direction.
"""

from __future__ import annotations

from typing import Any

# Attenuation applied to a tag inherited from a sector ETF rather than placed
# on the symbol directly.
SECTOR_PROPAGATION_WEIGHT = 0.5

# Tags that describe a sector's condition and therefore say something about its
# constituents. Symbol-specific tags (an earnings catalyst, an X-feed mention)
# are not propagated: they are about the ETF or about one company, not the group.
PROPAGATABLE_TAGS = frozenset(
    {
        "sector_rerating_up",
        "sector_rerating_down",
        "macro_shock",
        "macro_relief",
    }
)

# Major holdings by sector ETF. A curated approximation of the largest
# constituents, not an index-weighted replica — it exists to route a sector
# signal to plausibly affected names, and is intentionally conservative:
# a name absent here simply does not inherit the tag.
#
# Broad-market funds (SPY, QQQ, IWM) and single-asset funds (GLD, IBIT) have no
# entry by design.
SECTOR_CONSTITUENTS: dict[str, tuple[str, ...]] = {
    "XLK": ("AAPL", "MSFT", "NVDA", "AVGO", "CRM", "AMD", "ADBE", "ORCL", "CSCO", "ACN", "INTC", "QCOM", "TXN", "NOW", "PLTR", "MRVL"),
    "XLC": ("META", "GOOGL", "GOOG", "NFLX", "TMUS", "DIS", "VZ", "T", "EA"),
    "XLY": ("AMZN", "TSLA", "HD", "MCD", "BKNG", "LOW", "TJX", "NKE", "SBUX"),
    "XLF": ("BRK.B", "JPM", "V", "MA", "BAC", "WFC", "GS", "MS", "SPGI", "BLK"),
    "XLV": ("LLY", "UNH", "JNJ", "ABBV", "MRK", "TMO", "ABT", "PFE", "DHR", "AMGN"),
    "XLE": ("XOM", "CVX", "COP", "SLB", "EOG", "MPC", "PSX", "VLO", "OXY", "WMB"),
    "XLI": ("GE", "CAT", "RTX", "UNP", "HON", "BA", "DE", "LMT", "UPS", "AVAV"),
    "XLP": ("PG", "COST", "WMT", "KO", "PEP", "PM", "MO", "MDLZ"),
    "XLU": ("NEE", "SO", "DUK", "CEG", "AEP", "SRE", "VST"),
    "XLRE": ("PLD", "AMT", "EQIX", "WELL", "SPG", "PSA", "O"),
    "XLB": ("LIN", "SHW", "APD", "ECL", "FCX", "NEM"),
    "SMH": ("NVDA", "AVGO", "AMD", "QCOM", "TXN", "INTC", "MU", "AMAT", "LRCX", "KLAC", "ARM"),
    "SOXX": ("NVDA", "AVGO", "AMD", "QCOM", "TXN", "INTC", "MU", "AMAT", "LRCX", "KLAC"),
}

# Reverse index, built once.
_CONSTITUENT_SECTORS: dict[str, tuple[str, ...]] = {}
for _etf, _members in SECTOR_CONSTITUENTS.items():
    for _m in _members:
        _CONSTITUENT_SECTORS.setdefault(_m, ())
        _CONSTITUENT_SECTORS[_m] += (_etf,)


def sectors_for(symbol: str) -> tuple[str, ...]:
    """Sector ETFs that carry ``symbol`` as a constituent."""
    return _CONSTITUENT_SECTORS.get(symbol.upper(), ())


def tag_weight(tag: dict[str, Any]) -> float:
    """Weight of a tag: 1.0 when placed directly, less when inherited."""
    try:
        w = float(tag.get("weight", 1.0))
    except (TypeError, ValueError):
        return 1.0
    return max(0.0, min(1.0, w))


def propagate_sector_tags(symbol: str, tags: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return ``symbol``'s own tags plus attenuated ones inherited from its sectors.

    ``tags`` is the full tag set across symbols. Direct tags keep weight 1.0;
    inherited tags carry ``SECTOR_PROPAGATION_WEIGHT`` and record their origin
    in ``derived_from`` so a reader can tell where the signal came from.

    A symbol that already holds a tag directly does not also inherit the same
    tag key — the direct, stronger version wins.
    """
    symbol = symbol.upper()
    own = [t for t in tags if str(t.get("symbol", "")).upper() == symbol]
    own_keys = {str(t.get("tag_key", "")) for t in own}

    sectors = set(sectors_for(symbol))
    if not sectors:
        return list(own)

    inherited: list[dict[str, Any]] = []
    seen: set[str] = set()
    for t in tags:
        etf = str(t.get("symbol", "")).upper()
        key = str(t.get("tag_key", ""))
        if etf not in sectors or key not in PROPAGATABLE_TAGS:
            continue
        if key in own_keys or key in seen:
            continue
        seen.add(key)
        inherited.append(
            {
                **t,
                "symbol": symbol,
                "weight": SECTOR_PROPAGATION_WEIGHT,
                "derived_from": etf,
            }
        )

    return list(own) + inherited
