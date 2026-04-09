from fastapi import APIRouter

router = APIRouter(tags=["backtests"])


@router.get("/backtests/health")
def backtests_health():
    return {"ok": True}
