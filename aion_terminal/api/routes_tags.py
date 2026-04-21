from fastapi import APIRouter

router = APIRouter(tags=["tags"])


@router.get("/tags/health")
def tags_health():
    return {"ok": True}
