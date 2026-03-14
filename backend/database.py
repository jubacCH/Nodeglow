"""
Backwards-compatibility shim – all models and helpers now live in models/.

Everything is re-exported so existing ``from database import …`` statements
keep working unchanged.
"""
from models.base import (          # noqa: F401 – re-export
    Base,
    engine,
    AsyncSessionLocal,
    encrypt_value,
    decrypt_value,
    get_db,
)
from models.settings import (      # noqa: F401
    Setting,
    User,
    Session,
    get_current_user,
    get_setting,
    set_setting,
    is_setup_complete,
)
from models.ping import PingHost, PingResult   # noqa: F401
from models import init_db                      # noqa: F401
