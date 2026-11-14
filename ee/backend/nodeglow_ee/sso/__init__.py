"""Single sign-on per tenant (enterprise): OIDC identity providers.

See ``docs/specs/2026-11-14-sso-per-tenant-design.md``. The schema is core
(``models.identity``, revision 052); this package holds the logic.

What exists so far (implementation plan, pull request 1):

* :mod:`.config` — validation of a provider's settings and the shape the API
  returns (never the client secret);
* :mod:`.discovery` — "check connection": the provider's discovery document
  and signing keys, fetched through the guarded HTTP client;
* :mod:`.api` — the administration routes (``/api/identity-providers``) and
  the operator's installation setting for private issuer addresses;
* :mod:`.cleanup` — what deleting a provider removes;
* :mod:`.oidc` — the protocol: PKCE, state, nonce, token exchange, id token
  checks (pull request 2);
* :mod:`.flow` — the test sign-in: start, callback, result (pull request 2).

Nobody can sign in through a provider yet; only a test sign-in exists, which
creates no account and no session. :data:`SIGN_IN_RELEASED` keeps it
that way whatever the licence says (a ``*`` licence grants features added
later, this one included): providers cannot be enabled while it is False.
It becomes True in the release step after the security review.

Licence (feature ``sso``, owner decision 4): the administration needs it.
After expiry the providers stay readable and can be deleted, nothing else
(owner decision 8).
"""
from extensions import Registry

FEATURE = "sso"

# Off until the sign-in flow has passed the independent security review.
SIGN_IN_RELEASED = False


def register(registry: Registry) -> None:
    from nodeglow_ee.sso import api, flow

    registry.add_router(api.router)
    registry.add_router(flow.router)
    registry.add_scheduler_hook(flow.schedule)
    registry.after_restore(after_restore)
    registry.enable_feature(FEATURE)


async def after_restore(db) -> int:
    """A restored backup (from a later version, or edited) may carry enabled
    providers; while sign-in is not released they are switched off again.
    Returns the number of providers changed. The sign-in itself must check
    :data:`SIGN_IN_RELEASED` too, not only the settings."""
    if SIGN_IN_RELEASED:
        return 0
    import logging

    from sqlalchemy import or_, update

    from models.identity import IdentityProvider
    from services.tenancy import system_scope

    with system_scope("sso: restored providers stay off while sign-in is not released"):
        result = await db.execute(
            update(IdentityProvider)
            .where(or_(IdentityProvider.enabled.is_(True), IdentityProvider.sso_required.is_(True)))
            .values(enabled=False, sso_required=False)
            .execution_options(synchronize_session=False)
        )
        await db.flush()
    changed = result.rowcount or 0
    if changed:
        logging.getLogger("nodeglow.ee.sso").warning(
            "restore: %d identity provider(s) came back enabled or required; switched off, "
            "sign-in through single sign-on is not released in this version", changed)
    return changed
