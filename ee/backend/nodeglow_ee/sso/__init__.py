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
* :mod:`.cleanup` — what deleting a provider removes.

Nobody can sign in through a provider yet. :data:`SIGN_IN_RELEASED` keeps it
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
    from nodeglow_ee.sso import api

    registry.add_router(api.router)
    registry.enable_feature(FEATURE)
