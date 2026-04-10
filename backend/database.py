"""
Backwards-compatibility shim – all models and helpers now live in models/.

DEPRECATED: New code should import from ``models`` or ``models.base`` directly.
This module only re-exports for legacy compatibility and will be removed in a
future cleanup pass.
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
from models.ping import PingHost   # noqa: F401
from models import init_db                      # noqa: F401
