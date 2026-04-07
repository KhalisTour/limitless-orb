from fastapi import APIRouter

router = APIRouter(tags=["rankings"])


@router.get("/rankings/health")
def rankings_health():
    return {"ok": True}
