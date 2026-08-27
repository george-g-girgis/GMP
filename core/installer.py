"""
core/installer.py — Windows system integration & uninstaller registration.

Manages:
1. Start Menu shortcut (.lnk) in %APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs
2. Windows Control Panel / Settings "Installed Apps" registration via
   HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\GMP
3. Full clean uninstallation of shortcuts, autostart, and registry entries.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import winreg
from pathlib import Path

from core.config import ConfigManager, VERSION

log = logging.getLogger(__name__)

_UNINSTALL_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\GMP"


def get_project_root() -> Path:
    """Get the absolute root path of the GMP application."""
    return Path(__file__).resolve().parent.parent


def get_start_menu_shortcut_path() -> Path:
    """Get the path to the Start Menu shortcut."""
    appdata = os.environ.get("APPDATA", str(Path.home()))
    programs = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    return programs / "GMP.lnk"


def create_start_menu_shortcut() -> bool:
    """Create or update the Windows Start Menu shortcut."""
    root = get_project_root()
    shortcut_path = get_start_menu_shortcut_path()
    vbs_path = root / "Launch GMP.vbs"
    ico_path = root / "assets" / "app.ico"

    target = str(vbs_path) if vbs_path.exists() else str(root / ".venv" / "Scripts" / "pythonw.exe")
    args = "" if vbs_path.exists() else f'"{root / "main.py"}"'
    icon = str(ico_path) if ico_path.exists() else target

    try:
        # Create via Windows Script Host (WScript.Shell COM)
        import win32com.client  # type: ignore
        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortCut(str(shortcut_path))
        shortcut.TargetPath = target
        if args:
            shortcut.Arguments = args
        shortcut.WorkingDirectory = str(root)
        shortcut.Description = "GMP — Glass Media Player for Windows 11"
        shortcut.IconLocation = f"{icon},0"
        shortcut.save()
        log.info("Start Menu shortcut created: %s", shortcut_path)
        return True
    except Exception as exc:
        log.warning("win32com shortcut creation failed (%s), falling back to PowerShell", exc)
        try:
            import subprocess
            ps_script = (
                f'$ws = New-Object -ComObject WScript.Shell; '
                f'$s = $ws.CreateShortcut("{shortcut_path}"); '
                f'$s.TargetPath = "{target}"; '
                f'$s.WorkingDirectory = "{root}"; '
                f'$s.Description = "GMP — Glass Media Player for Windows 11"; '
                f'$s.IconLocation = "{icon},0"; '
                f'$s.Save()'
            )
            subprocess.run(["powershell", "-NoProfile", "-Command", ps_script], check=True, capture_output=True)
            log.info("Start Menu shortcut created via PowerShell: %s", shortcut_path)
            return True
        except Exception as e:
            log.error("Failed to create Start Menu shortcut: %s", e)
            return False


def remove_start_menu_shortcut() -> bool:
    """Remove the Start Menu shortcut if it exists."""
    shortcut_path = get_start_menu_shortcut_path()
    try:
        if shortcut_path.exists():
            shortcut_path.unlink()
            log.info("Start Menu shortcut removed: %s", shortcut_path)
        return True
    except Exception as exc:
        log.error("Failed to remove Start Menu shortcut: %s", exc)
        return False


def register_uninstall_entry() -> bool:
    """
    Register GMP in Windows Control Panel / Settings 'Installed Apps'
    (HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\GMP).
    """
    root = get_project_root()
    ico_path = root / "assets" / "app.ico"
    uninstall_py = root / "uninstall.py"
    pythonw = root / ".venv" / "Scripts" / "pythonw.exe"
    if not pythonw.exists():
        pythonw = Path(sys.executable)

    uninstall_cmd = f'"{pythonw}" "{uninstall_py}"'
    quiet_uninstall_cmd = f'"{pythonw}" "{uninstall_py}" --silent'

    try:
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, _UNINSTALL_REG_KEY)
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, "GMP — Glass Media Player")
        winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, VERSION)
        winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, "George Girgis")
        winreg.SetValueEx(key, "DisplayIcon", 0, winreg.REG_SZ, str(ico_path) if ico_path.exists() else str(pythonw))
        winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, str(root))
        winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ, uninstall_cmd)
        winreg.SetValueEx(key, "QuietUninstallString", 0, winreg.REG_SZ, quiet_uninstall_cmd)
        winreg.SetValueEx(key, "HelpLink", 0, winreg.REG_SZ, "https://github.com/george-g-girgis/GMP")
        winreg.SetValueEx(key, "URLInfoAbout", 0, winreg.REG_SZ, "https://github.com/george-g-girgis/GMP")
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)
        winreg.CloseKey(key)
        log.info("Registered in Windows Control Panel Programs & Features")
        return True
    except OSError as exc:
        log.error("Failed to register Windows Uninstall entry: %s", exc)
        return False


def unregister_uninstall_entry() -> bool:
    """Remove GMP from Windows Control Panel Uninstall list."""
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, _UNINSTALL_REG_KEY)
        log.info("Removed Windows Uninstall registry entry")
        return True
    except FileNotFoundError:
        return True
    except OSError as exc:
        log.error("Failed to remove Windows Uninstall registry entry: %s", exc)
        return False


def install_system_entries() -> bool:
    """Register both Start Menu shortcut and Windows Add/Remove Programs entry."""
    s1 = create_start_menu_shortcut()
    s2 = register_uninstall_entry()
    return s1 and s2


def uninstall(remove_user_data: bool = True) -> bool:
    """
    Perform a complete uninstall of GMP system registrations:
    - Removes Start Menu shortcut
    - Removes Autostart registry key
    - Removes Control Panel Uninstall entry
    - Cleans config & cache data if requested
    """
    from core.autostart import disable as disable_autostart

    log.info("Starting GMP uninstallation...")
    disable_autostart()
    remove_start_menu_shortcut()
    unregister_uninstall_entry()

    if remove_user_data:
        cfg_dir = ConfigManager.config_dir()
        if cfg_dir.exists():
            try:
                shutil.rmtree(cfg_dir, ignore_errors=True)
                log.info("Removed config directory: %s", cfg_dir)
            except Exception as e:
                log.warning("Could not remove config dir: %s", e)

        cache_dir = ConfigManager.cache_dir()
        if cache_dir.exists():
            try:
                shutil.rmtree(cache_dir, ignore_errors=True)
                log.info("Removed cache directory: %s", cache_dir)
            except Exception as e:
                log.warning("Could not remove cache dir: %s", e)

    log.info("GMP uninstallation completed.")
    return True
