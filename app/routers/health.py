from fastapi import APIRouter
from ..db.factory import get_adapter

router = APIRouter(prefix="/health", tags=["health"])

@router.get("")
def health():
    return {"status": "healthy"}

@router.get("/db")
def health_db():
    rows, _ = get_adapter().fetch_all("select 1")
    return {"db": "ok", "result": rows[0][0] if rows else None}