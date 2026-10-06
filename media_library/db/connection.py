"""Database connection factory."""

import os
import sqlite3
from pathlib import Path
from typing import Optional, Union
from media_library.core.config import DB_PATH

def get_current_db_path(custom_path: Optional[Union[str, Path]] = None) -> Path:
    """Returns the resolved DB Path respecting os.environ['DB_PATH']."""
    if custom_path:
        return Path(custom_path)
    elif os.environ.get("DB_PATH"):
        return Path(os.environ["DB_PATH"]).resolve()
    return DB_PATH

def get_db(custom_path: Optional[Union[str, Path]] = None) -> sqlite3.Connection:
    """Returns a SQLite connection configured with WAL mode and row factory."""
    path = get_current_db_path(custom_path)
    conn = sqlite3.connect(path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn
