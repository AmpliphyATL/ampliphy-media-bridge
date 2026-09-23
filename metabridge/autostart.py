"""Start-with-Windows support.

Registers the running program in the per-user Windows startup list
(HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run), the same mechanism
DCS Console and PlayoutONE use. No admin rights needed.

On non-Windows systems every function is a harmless no-op.
"""
from __future__ import annotations

import os
import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "AmpliPhyMetaBridge"
AUTOSTART_FLAG = "--autostart"


def is_windows() -> bool:
    return sys.platform.startswith("win")


def launch_command() -> str:
    """The exact command Windows should run at login."""
    if getattr(sys, "frozen", False):
        exe = os.environ.get("METABRIDGE_AUTOSTART_EXE") or sys.executable
        return f'"{exe}" {AUTOSTART_FLAG}'
    # Running from source: python + launcher script
    script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "metabridge_launcher.py")
    pythonw = sys.executable.replace("python.exe", "pythonw.exe")
    return f'"{pythonw}" "{script}" {AUTOSTART_FLAG}'


def is_enabled() -> bool:
    if not is_windows():
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as k:
            value, _ = winreg.QueryValueEx(k, APP_NAME)
            return bool(value)
    except OSError:
        return False


def enable() -> bool:
    """Register (or refresh) the startup entry. Returns True on success."""
    if not is_windows():
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, launch_command())
        return True
    except OSError:
        return False


def disable() -> bool:
    if not is_windows():
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, APP_NAME)
        return True
    except FileNotFoundError:
        return True  # already gone
    except OSError:
        return False


def set_enabled(on: bool) -> bool:
    return enable() if on else disable()
