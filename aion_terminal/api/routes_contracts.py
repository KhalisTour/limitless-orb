from fastapi import APIRouter

router = APIRouter(tags=["contracts"])


@router.get("/contracts/health")
def contracts_health():
    return {"ok": True}
