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
        # Everything registers; each feature checks the license when it is
        # used (nodeglow_ee.license_runtime), so a license installed or
        # removed in Settings applies without a restart. Without a usable
        # license the features stay inactive and the core is unaffected.
        from nodeglow_ee import ai, ha, license_runtime

        license_runtime.register(registry)
        ha.register(registry)
        ai.register(registry)


plugin = EnterprisePlugin()
