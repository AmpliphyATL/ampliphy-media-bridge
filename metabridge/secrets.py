"""Protect secrets at rest with Windows DPAPI.

A value encrypted here can only be decrypted by the same Windows user account
on the same machine - copying metabridge.env.json elsewhere yields nothing
useful. On non-Windows systems the value is stored as-is (development only).

Stored form:  "dpapi:<base64>"   (plain text is still accepted for upgrades)
"""
from __future__ import annotations

import base64
import ctypes
import sys

PREFIX = "dpapi:"


def _is_windows() -> bool:
    return sys.platform.startswith("win")


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _crypt(data: bytes, protect: bool) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))  # must outlive the call
    src = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = _DATA_BLOB()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    # CRYPTPROTECT_UI_FORBIDDEN = 0x1 : never pop a dialog
    if not fn(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(out)):
        raise OSError(ctypes.get_last_error() or "DPAPI call failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def protect(value: str) -> str:
    """Encrypt for storage. Returns the value unchanged if empty or not on Windows."""
    if not value or not _is_windows():
        return value
    try:
        return PREFIX + base64.b64encode(_crypt(value.encode("utf-8"), True)).decode("ascii")
    except Exception:
        return value  # never lose the setting because encryption failed


def reveal(stored: str) -> str:
    """Decrypt a stored value; plain (legacy) values pass through untouched."""
    if not stored or not stored.startswith(PREFIX):
        return stored or ""
    if not _is_windows():
        return ""
    try:
        return _crypt(base64.b64decode(stored[len(PREFIX):]), False).decode("utf-8")
    except Exception:
        return ""


def is_protected(stored: str) -> bool:
    return bool(stored) and stored.startswith(PREFIX)
