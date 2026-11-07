"""Shared error translation for the mutation routers."""

from __future__ import annotations

import logging
import sqlite3

from fastapi import HTTPException

from media_library.db import DatabaseReadOnlyError
from media_library.writes import WriteError

_log = logging.getLogger(__name__)


def as_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, WriteError):
        return HTTPException(status_code=exc.status, detail=exc.message)
    if isinstance(exc, DatabaseReadOnlyError):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=403, detail="Permission denied")
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail="File or folder not found")
    if isinstance(exc, sqlite3.Error):
        return HTTPException(status_code=503, detail=f"Index unavailable: {exc}")
    # Never forward raw exception text: OSError/subprocess messages carry
    # absolute filesystem paths and other internals.
    _log.exception("Unexpected error in a mutation route", exc_info=exc)
    return HTTPException(status_code=500, detail="Internal error")
