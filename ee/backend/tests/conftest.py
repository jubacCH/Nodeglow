"""Enterprise tests. Collected from backend/ via pytest.ini testpaths.

They reuse the core fixtures (``db``, ``make_client``): ``backend/`` and
``ee/backend/`` are put on ``sys.path`` here so both the core modules and
``nodeglow_ee`` import as they do in the running app.

With ``NODEGLOW_DISABLE_EE=1`` the whole directory is skipped: the core suite
then runs exactly as it would without ee/.

Every test runs with a valid all-features license signed by a throwaway test
key (fixture ``ee_license``); license tests switch to other states with it.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_EE_BACKEND = os.path.dirname(_HERE)
_CORE_BACKEND = os.path.join(_EE_BACKEND, "..", "..", "backend")

if os.environ.get("NODEGLOW_DISABLE_EE", "").strip().lower() in ("1", "true", "yes", "on"):
    collect_ignore_glob = ["*"]
else:
    for _p in (os.path.abspath(_CORE_BACKEND), _EE_BACKEND, _HERE):
        if _p not in sys.path:
            sys.path.insert(0, _p)

    os.environ.setdefault("SECRET_KEY", "test-secret-key-for-pytest")
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    os.environ.setdefault("DATA_DIR", os.path.join(os.path.abspath(_CORE_BACKEND), "tests", ".test_data"))

    import pytest  # noqa: E402

    from tests.conftest import db  # noqa: E402,F401  (core fixture, re-exported)
    from ee_license_helpers import TEST_KID, TEST_SIGNING_KEY, issue_test_license  # noqa: E402

    @pytest.fixture(autouse=True)
    def ee_license(monkeypatch):
        """Every enterprise test runs licensed (all features) unless it says otherwise.

        The test key is trusted only inside the test process (``TRUSTED_KEYS``
        is patched in memory). ``ee_license.set(text)`` puts a license into
        ``NODEGLOW_LICENSE``; ``ee_license.clear()`` removes it.
        """
        from nodeglow_ee import license_runtime, licensing

        monkeypatch.setitem(licensing.TRUSTED_KEYS, TEST_KID,
                            TEST_SIGNING_KEY.public_key().public_bytes_raw().hex())

        class Control:
            def set(self, text: str | None) -> None:
                if text:
                    monkeypatch.setenv(license_runtime.LICENSE_ENV, text)
                else:
                    monkeypatch.delenv(license_runtime.LICENSE_ENV, raising=False)
                license_runtime.manager.reset()

            def clear(self) -> None:
                self.set(None)

        control = Control()
        control.set(issue_test_license())
        yield control
        license_runtime.manager.reset()
