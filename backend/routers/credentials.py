"""Credential Store – CRUD routes for encrypted credentials."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from templating import templates

from models.base import get_db
from models.credential import Credential
from services.snmp import decrypt_credential, encrypt_credential

router = APIRouter()

# Credential type definitions for the UI
CREDENTIAL_TYPES = {
    "snmp_v2c": {
        "label": "SNMP v2c",
        "fields": [
            {"name": "community", "label": "Community String", "type": "password", "required": True},
        ],
    },
    "snmp_v3": {
        "label": "SNMP v3",
        "fields": [
            {"name": "username", "label": "Username", "type": "text", "required": True},
            {"name": "auth_protocol", "label": "Auth Protocol", "type": "select",
             "options": ["SHA", "SHA256", "MD5"], "default": "SHA"},
            {"name": "auth_password", "label": "Auth Password", "type": "password"},
            {"name": "priv_protocol", "label": "Privacy Protocol", "type": "select",
             "options": ["AES", "AES256", "DES"], "default": "AES"},
            {"name": "priv_password", "label": "Privacy Password", "type": "password"},
        ],
    },
    "winrm": {
        "label": "WinRM",
        "fields": [
            {"name": "username", "label": "Username", "type": "text", "required": True},
            {"name": "password", "label": "Password", "type": "password", "required": True},
            {"name": "transport", "label": "Transport", "type": "select",
             "options": ["ntlm", "kerberos", "basic"], "default": "ntlm"},
        ],
    },
    "ssh": {
        "label": "SSH",
        "fields": [
            {"name": "username", "label": "Username", "type": "text", "required": True},
            {"name": "password", "label": "Password", "type": "password"},
            {"name": "private_key", "label": "Private Key", "type": "textarea"},
        ],
    },
}


@router.get("/credentials")
async def credentials_page(request: Request, db: AsyncSession = Depends(get_db)):
    q = await db.execute(select(Credential).order_by(Credential.type, Credential.name))
    creds = q.scalars().all()
    # Decrypt to show names but mask secrets
    cred_list = []
    for c in creds:
        data = decrypt_credential(c.data_json)
        # Mask sensitive fields
        masked = {}
        type_def = CREDENTIAL_TYPES.get(c.type, {})
        for field in type_def.get("fields", []):
            val = data.get(field["name"], "")
            if field["type"] == "password" and val:
                masked[field["name"]] = "••••••••"
            else:
                masked[field["name"]] = val
        cred_list.append({
            "id": c.id, "name": c.name, "type": c.type,
            "type_label": type_def.get("label", c.type),
            "fields": masked,
            "created_at": c.created_at,
        })
    return templates.TemplateResponse("credentials.html", {
        "request": request,
        "credentials": cred_list,
        "credential_types": CREDENTIAL_TYPES,
    })


@router.post("/api/credentials")
async def api_create_credential(request: Request, db: AsyncSession = Depends(get_db)):
    body = await request.json()
    name = (body.get("name") or "").strip()
    cred_type = (body.get("type") or "").strip()

    if not name:
        return JSONResponse({"error": "Name required"}, status_code=400)
    if cred_type not in CREDENTIAL_TYPES:
        return JSONResponse({"error": f"Unknown type: {cred_type}"}, status_code=400)

    # Extract credential fields
    data = {}
    for field in CREDENTIAL_TYPES[cred_type]["fields"]:
        data[field["name"]] = body.get(field["name"], field.get("default", ""))

    cred = Credential(
        name=name,
        type=cred_type,
        data_json=encrypt_credential(data),
    )
    db.add(cred)
    await db.commit()
    await db.refresh(cred)

    return JSONResponse({"id": cred.id, "name": cred.name})


@router.put("/api/credentials/{cred_id}")
async def api_update_credential(cred_id: int, request: Request,
                                db: AsyncSession = Depends(get_db)):
    body = await request.json()
    q = await db.execute(select(Credential).where(Credential.id == cred_id))
    cred = q.scalar_one_or_none()
    if not cred:
        return JSONResponse({"error": "Not found"}, status_code=404)

    if body.get("name"):
        cred.name = body["name"]

    # Update credential data – only overwrite non-empty fields
    existing_data = decrypt_credential(cred.data_json)
    type_def = CREDENTIAL_TYPES.get(cred.type, {})
    for field in type_def.get("fields", []):
        new_val = body.get(field["name"])
        if new_val is not None and new_val != "":
            existing_data[field["name"]] = new_val

    cred.data_json = encrypt_credential(existing_data)
    await db.commit()

    return JSONResponse({"ok": True})


@router.delete("/api/credentials/{cred_id}")
async def api_delete_credential(cred_id: int, db: AsyncSession = Depends(get_db)):
    await db.execute(delete(Credential).where(Credential.id == cred_id))
    await db.commit()
    return JSONResponse({"ok": True})


@router.get("/api/credentials/list")
async def api_list_credentials(db: AsyncSession = Depends(get_db)):
    """List credentials (id + name + type only, no secrets)."""
    q = await db.execute(select(Credential).order_by(Credential.name))
    creds = [{"id": c.id, "name": c.name, "type": c.type} for c in q.scalars().all()]
    return JSONResponse({"credentials": creds})
