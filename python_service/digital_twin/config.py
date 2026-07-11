from .domain.accounts import AccountConfig, configured, split_symbols
from .domain.parsing import parse_assignments
from .infrastructure.settings import (
    DEFAULT_DATA_DIR,
    ROOT_DIR,
    data_dir,
    load_env_file,
    load_local_env,
    read_json,
    read_settings_store,
    runtime_settings,
    save_runtime_settings,
    settings_path,
    utc_now,
    write_private_json,
    write_settings_store,
)


class AccountRegistry:
    def __new__(cls, *args, **kwargs):
        if args or kwargs:
            raise TypeError("Path-based AccountRegistry construction is no longer supported; use the operational store.")
        from .infrastructure.operational_store import account_registry

        return account_registry()

__all__ = [
    "AccountConfig",
    "AccountRegistry",
    "DEFAULT_DATA_DIR",
    "ROOT_DIR",
    "configured",
    "data_dir",
    "load_env_file",
    "load_local_env",
    "parse_assignments",
    "read_json",
    "read_settings_store",
    "runtime_settings",
    "save_runtime_settings",
    "settings_path",
    "split_symbols",
    "utc_now",
    "write_private_json",
    "write_settings_store",
]
