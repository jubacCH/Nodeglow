"""Settings package — composes per-topic sub-routers under /settings.

Splitting the original 845-line settings.py into focused modules:
- general.py       : /json, /save, /phpipam/*, /geoip/download
- notifications.py : /notifications/*, /digest/save
- api_keys.py      : /api-keys/*
- ai.py            : /ai/save, /ai/test-summary, /ai/usage
- ldap.py          : /ldap/save, /ldap/test

main.py imports `routers.settings` exactly as before; the public `router`
attribute is composed here.
"""
from fastapi import APIRouter

from . import ai, api_keys, general, ldap, notifications

router = APIRouter(prefix="/settings")
router.include_router(general.router)
router.include_router(notifications.router)
router.include_router(api_keys.router)
router.include_router(ai.router)
router.include_router(ldap.router)
