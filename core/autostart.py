"""
core/autostart.py — Windows login autostart via the registry.

Writes (or removes) a value under::

    HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run

The value points to the ``Launch GMP.vbs`` script using its absolute
path so the app can start from any working directory.
"""

from __future__ import annotations

import logging
import sys
import winreg
from pathlib import Path

log = logging.getLogger(__name__)

_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "GMP"


def _vbs_path() -> str:
    """Resolve the absolute path to the VBS launcher."""
    base = Path(sys.argv[0]).resolve().parent
    vbs = base / "Launch GMP.vbs"
    if vbs.exists():
        return f'wscript.exe "{vbs}"'
    # Fallback: direct pythonw invocation
    py = base / ".venv" / "Scripts" / "pythonw.exe"
    main = base / "main.py"
    return f'"{py}" "{main}"'


def enable() -> bool:
    """Register GMP to run at login.  Returns True on success."""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _KEY_PATH, 0, winreg.KEY_SET_VALUE,
        )
        winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, _vbs_path())
        winreg.CloseKey(key)
        log.info("Autostart enabled")
        return True
    except OSError as exc:
        log.error("Failed to enable autostart: %s", exc)
        return False


def disable() -> bool:
    """Remove GMP from login startup.  Returns True on success."""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _KEY_PATH, 0, winreg.KEY_SET_VALUE,
        )
        try:
            winreg.DeleteValue(key, _VALUE_NAME)
        except FileNotFoundError:
            pass  # Already absent
        winreg.CloseKey(key)
        log.info("Autostart disabled")
        return True
    except OSError as exc:
        log.error("Failed to disable autostart: %s", exc)
        return False


def is_enabled() -> bool:
    """Check whether GMP is currently set to run at login."""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _KEY_PATH, 0, winreg.KEY_READ,
        )
        try:
            winreg.QueryValueEx(key, _VALUE_NAME)
            winreg.CloseKey(key)
            return True
        except FileNotFoundError:
            winreg.CloseKey(key)
            return False
    except OSError:
        return False


def set_enabled(on: bool) -> bool:
    """Enable or disable autostart.  Returns True on success."""
    return enable() if on else disable()
