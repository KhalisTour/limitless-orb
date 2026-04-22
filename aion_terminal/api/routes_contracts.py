from __future__ import annotations

from fastapi import APIRouter

from aion_terminal.services.ranking_service import rank_universe

router = APIRouter(tags=["contracts"])


@router.get("/contracts/health")
def contracts_health():
    return {"ok": True}


@router.get("/contracts/universe")
def contracts_universe():
    rankings = rank_universe()
    return [
        {
            "symbol": r.symbol,
            "bias": r.signals[0]["bias"] if r.signals else "bearish",
            "best": r.best_contract,
            "safer": r.safer_contract,
            "convex": r.convex_contract,
            "warnings": r.contract_warnings,
        }
        for r in rankings
    ]
