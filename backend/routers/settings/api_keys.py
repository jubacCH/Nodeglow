"""API key management — list, create, delete."""
import hashlib
import hmac
import os

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.api_key import ApiKey
from ratelimit import rate_limit

from ._helpers import require_admin

router = APIRouter()


@router.get("/api-keys")
async def api_keys_list(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ApiKey).order_by(ApiKey.created_at.desc()))
    keys = result.scalars().all()
    return JSONResponse([
        {"id": k.id, "name": k.name, "prefix": k.prefix, "role": k.role,
         "enabled": k.enabled,
         "last_used": k.last_used.isoformat() if k.last_used else None,
         "created_at": k.created_at.isoformat() if k.created_at else None}
        for k in keys
    ])


@router.post("/api-keys/create")
@rate_limit(max_requests=10, window_seconds=60)
async def create_api_key(request: Request, db: AsyncSession = Depends(get_db)):
    if err := require_admin(request):
        return err
    body = await request.json()
    name = (body.get("name") or "").strip()
    role = body.get("role", "readonly")
    if not name:
        return JSONResponse({"error": "Name is required"}, status_code=400)
    if role not in ("readonly", "editor", "admin"):
        return JSONResponse({"error": "Invalid role"}, status_code=400)

    from config import SECRET_KEY
    raw_key = f"ng_{os.urandom(24).hex()}"
    prefix = raw_key[:8]
    key_hash = hmac.new(SECRET_KEY.encode(), raw_key.encode(), hashlib.sha256).hexdigest()

    user = getattr(request.state, "current_user", None)
    api_key = ApiKey(
        name=name, key_hash=key_hash, prefix=prefix, role=role,
        created_by=user.username if user else None,
    )
    db.add(api_key)
    await db.commit()
    return JSONResponse({"ok": True, "key": raw_key, "id": api_key.id, "prefix": prefix})


@router.delete("/api-keys/{key_id}")
@rate_limit(max_requests=10, window_seconds=60)
async def delete_api_key(key_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    if err := require_admin(request):
        return err
    key = await db.get(ApiKey, key_id)
    if not key:
        return JSONResponse({"error": "Not found"}, status_code=404)
    await db.delete(key)
    await db.commit()
    return JSONResponse({"ok": True})
