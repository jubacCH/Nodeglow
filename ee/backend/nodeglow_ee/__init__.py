"""Nodeglow Enterprise — commercial features (Nodeglow Enterprise License, draft).

Loaded by the core's ``ee_loader`` (entry point ``nodeglow.plugins`` →
``nodeglow_ee:plugin``, or from the source tree). This package may import the
core; the core never imports it. See ``ee/README.md``.

The backend's own directory (``backend/``) must be importable, as it is when
the app runs: modules here import ``services``, ``routers``, ``models`` and
``extensions`` from the core.
"""

NAME = "ee"
EDITION = "enterprise"


class EnterprisePlugin:
    name = NAME
    EDITION = EDITION

    def register(self, registry) -> None:
        # TODO(license): verify the license key here before registering
        # anything (see ee/README.md, "License keys").
        from nodeglow_ee import ai, ha

        ha.register(registry)
        ai.register(registry)


plugin = EnterprisePlugin()
