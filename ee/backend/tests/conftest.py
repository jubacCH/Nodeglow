"""Enterprise tests. Collected from backend/ via pytest.ini testpaths.

They reuse the core fixtures (``db``, ``make_client``): ``backend/`` and
``ee/backend/`` are put on ``sys.path`` here so both the core modules and
``nodeglow_ee`` import as they do in the running app.

With ``NODEGLOW_DISABLE_EE=1`` the whole directory is skipped: the core suite
then runs exactly as it would without ee/.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_EE_BACKEND = os.path.dirname(_HERE)
_CORE_BACKEND = os.path.join(_EE_BACKEND, "..", "..", "backend")

if os.environ.get("NODEGLOW_DISABLE_EE", "").strip().lower() in ("1", "true", "yes", "on"):
    collect_ignore_glob = ["*"]
else:
    for _p in (os.path.abspath(_CORE_BACKEND), _EE_BACKEND):
        if _p not in sys.path:
            sys.path.insert(0, _p)

    os.environ.setdefault("SECRET_KEY", "test-secret-key-for-pytest")
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    os.environ.setdefault("DATA_DIR", os.path.join(os.path.abspath(_CORE_BACKEND), "tests", ".test_data"))

    from tests.conftest import db  # noqa: E402,F401  (core fixture, re-exported)
