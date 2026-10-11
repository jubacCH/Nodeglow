"""Shared setup of the multi-tenancy (phase 9) tests: an app with real auth,
the ee tenancy backend, users, sessions and a second tenant."""
from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import bcrypt
from sqlalchemy import select

from models.audit import AuditLog
from models.settings import Session, User, _hash_token
from models.tenant import Site, Tenant, TenantMembership
from services.tenancy import system_scope

PW = "Corr3ct-Horse!"


@dataclass
class Env:
    client: Any
    sf: Any

    async def user(self, username: str, memberships: dict[int, str], *,
                   is_operator: bool = False) -> int:
        async with self.sf() as s:
            u = User(username=username, role="admin", auth_source="local",
                     is_operator=is_operator,
                     password_hash=bcrypt.hashpw(PW.encode(), bcrypt.gensalt(4)).decode())
            s.add(u)
            await s.flush()
            for tid, role in memberships.items():
                s.add(TenantMembership(user_id=u.id, tenant_id=tid, role=role))
            await s.commit()
            return u.id

    async def session(self, user_id: int, tenant_id: int | None) -> str:
        token = secrets.token_hex(32)
        async with self.sf() as s:
            s.add(Session(token=_hash_token(token), user_id=user_id, active_tenant_id=tenant_id,
                          expires_at=datetime.utcnow() + timedelta(days=1)))
            await s.commit()
        return token

    def login_as(self, token: str) -> None:
        self.client.cookies.set("nodeglow_session", token)

    def signed_out(self) -> None:
        """No session cookie (the CSRF cookie stays, as in a browser)."""
        self.client.cookies.clear()
        self.client.cookies.set("ng_csrf", self.client.headers["x-csrf-token"])

    async def tenant(self, tid: int, name: str, *, status: str = "active",
                     plan: str | None = "standard", site_id: int | None = None) -> None:
        async with self.sf() as s:
            with system_scope("test: seed a tenant", audit=False):
                s.add(Tenant(id=tid, provider_id=1, name=name, slug=f"t{tid}", status=status,
                             plan=plan))
                await s.flush()
                s.add(Site(id=site_id or tid * 10, tenant_id=tid, name=f"{name} site"))
                await s.commit()

    async def audit(self, action: str) -> list[AuditLog]:
        async with self.sf() as s:
            with system_scope("test: read the audit log", audit=False):
                return list((await s.execute(
                    select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id)
                )).scalars().all())


@asynccontextmanager
async def tenancy_env():
    """make_client with real auth and a reset rate limiter / support audit window."""
    from services import shared_state
    from tests.test_routers.conftest import make_client

    from nodeglow_ee.tenancy import support

    shared_state.reset()
    support.reset_audit_window()
    async with make_client(fake_user=None) as (client, sf):
        yield Env(client=client, sf=sf)
    shared_state.reset()
    support.reset_audit_window()
