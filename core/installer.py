"""
core/installer.py — Windows system integration & uninstaller registration.

Manages:
1. Start Menu shortcut (.lnk) in %APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs
2. Windows Control Panel / Settings "Installed Apps" registration via
   HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\GMP
3. Full clean uninstallation of shortcuts, autostart, registry entries, and application data.
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
    if getattr(sys, "frozen", False):
        # When frozen with PyInstaller (onedir mode), sys.executable is in the root directory (GMP/GMP.exe)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def get_start_menu_shortcut_path() -> Path:
    """Get the path to the Start Menu shortcut."""
    appdata = os.environ.get("APPDATA", str(Path.home()))
    programs = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    return programs / "GMP.lnk"


def get_desktop_shortcut_path() -> Path:
    """Get the path to the Desktop shortcut."""
    userprofile = os.environ.get("USERPROFILE", str(Path.home()))
    return Path(userprofile) / "Desktop" / "GMP.lnk"


def create_start_menu_shortcut() -> bool:
    """Create or update the Windows Start Menu shortcut."""
    root = get_project_root()
    shortcut_path = get_start_menu_shortcut_path()
    ico_path = root / "assets" / "app.ico"

    if getattr(sys, "frozen", False):
        target = str(sys.executable)
        args = ""
        icon = str(ico_path) if ico_path.exists() else target
    else:
        vbs_path = root / "Launch GMP.vbs"
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


def remove_desktop_shortcut() -> bool:
    """Remove Desktop shortcut if present."""
    p = get_desktop_shortcut_path()
    try:
        if p.exists():
            p.unlink()
            log.info("Desktop shortcut removed: %s", p)
        return True
    except Exception as exc:
        log.error("Failed to remove Desktop shortcut: %s", exc)
        return False


def register_uninstall_entry() -> bool:
    """
    Register GMP in Windows Control Panel / Settings 'Installed Apps'
    (HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\GMP).
    """
    root = get_project_root()
    ico_path = root / "assets" / "app.ico"

    if getattr(sys, "frozen", False):
        display_icon = str(ico_path) if ico_path.exists() else str(sys.executable)
        uninstall_cmd = f'"{sys.executable}" --uninstall'
        quiet_uninstall_cmd = f'"{sys.executable}" --uninstall --silent'
    else:
        uninstall_py = root / "uninstall.py"
        pythonw = root / ".venv" / "Scripts" / "pythonw.exe"
        if not pythonw.exists():
            pythonw = Path(sys.executable)
        display_icon = str(ico_path) if ico_path.exists() else str(pythonw)
        uninstall_cmd = f'"{pythonw}" "{uninstall_py}"'
        quiet_uninstall_cmd = f'"{pythonw}" "{uninstall_py}" --silent'

    try:
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, _UNINSTALL_REG_KEY)
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, "GMP — Glass Media Player")
        winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, VERSION)
        winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, "GGG")
        winreg.SetValueEx(key, "DisplayIcon", 0, winreg.REG_SZ, display_icon)
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


def terminate_other_gmp_processes() -> None:
    """Terminate other running instances of GMP."""
    current_pid = os.getpid()
    try:
        import subprocess
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq GMP.exe", "/FO", "CSV", "/NH"],
            text=True, stderr=subprocess.DEVNULL
        )
        for line in out.strip().splitlines():
            parts = [p.strip(' "') for p in line.split(",")]
            if len(parts) >= 2 and parts[1].isdigit():
                pid = int(parts[1])
                if pid != current_pid:
                    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    except Exception as exc:
        log.warning("Could not terminate other GMP processes: %s", exc)


def schedule_app_directory_deletion(root_dir: Path) -> None:
    """Spawn a detached cmd process to delete the application directory after process exit."""
    try:
        import subprocess
        # Wait 2 seconds for current process to exit, then remove directory
        cmd = f'ping 127.0.0.1 -n 3 >nul & rd /s /q "{root_dir}"'
        subprocess.Popen(
            ["cmd.exe", "/c", cmd],
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
            close_fds=True
        )
        log.info("Scheduled application directory self-delete: %s", root_dir)
    except Exception as exc:
        log.warning("Could not schedule self-delete: %s", exc)


def install_system_entries() -> bool:
    """Register both Start Menu shortcut and Windows Add/Remove Programs entry."""
    s1 = create_start_menu_shortcut()
    s2 = register_uninstall_entry()
    return s1 and s2


def uninstall(remove_user_data: bool = True, remove_app_files: bool = False) -> bool:
    """
    Perform a complete uninstall of GMP system registrations:
    - Closes any running GMP instances
    - Removes Start Menu and Desktop shortcuts
    - Removes Autostart registry key
    - Removes Control Panel Uninstall entry
    - Cleans config & cache data if requested
    - Schedules self-delete of application files if requested
    """
    from core.autostart import disable as disable_autostart

    log.info("Starting GMP uninstallation...")
    terminate_other_gmp_processes()
    disable_autostart()
    remove_start_menu_shortcut()
    remove_desktop_shortcut()
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

        # Remove crash logs in project root
        root = get_project_root()
        for log_name in ("crash.txt", "crash_log.txt"):
            log_file = root / log_name
            if log_file.exists():
                try:
                    log_file.unlink()
                except Exception:
                    pass

    if remove_app_files:
        root = get_project_root()
        schedule_app_directory_deletion(root)

    log.info("GMP uninstallation completed.")
    return True


def run_uninstaller_gui_or_cli() -> None:
    """Entrypoint when uninstaller is invoked from CLI, Windows Uninstall, or desktop."""
    is_silent = "--silent" in sys.argv or "-s" in sys.argv
    is_frozen = getattr(sys, "frozen", False)

    if is_silent:
        uninstall(remove_user_data=True, remove_app_files=is_frozen)
        sys.exit(0)

    try:
        from PyQt6.QtGui import QIcon
        from PyQt6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance()
        if app is None:
            app = QApplication(sys.argv)
        ico_file = get_project_root() / "assets" / "app.ico"
        if ico_file.exists():
            app.setWindowIcon(QIcon(str(ico_file)))

        reply = QMessageBox.question(
            None,
            "Uninstall GMP",
            "Are you sure you want to completely uninstall GMP (Glass Media Player)?\n\n"
            "This will remove:\n"
            "• Start Menu and Desktop shortcuts\n"
            "• Windows startup registration\n"
            "• Settings and cached data\n"
            + ("• Application files\n" if is_frozen else ""),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            uninstall(remove_user_data=True, remove_app_files=is_frozen)
            QMessageBox.information(
                None,
                "GMP Uninstalled",
                "GMP (Glass Media Player) was successfully removed from your computer.",
            )
            sys.exit(0)
    except Exception:
        import ctypes
        MB_YESNO = 4
        MB_ICONQUESTION = 0x20
        IDYES = 6
        res = ctypes.windll.user32.MessageBoxW(
            0,
            "Are you sure you want to completely uninstall GMP (Glass Media Player)?",
            "Uninstall GMP",
            MB_YESNO | MB_ICONQUESTION,
        )
        if res == IDYES:
            uninstall(remove_user_data=True, remove_app_files=is_frozen)
            ctypes.windll.user32.MessageBoxW(
                0,
                "GMP was successfully removed from your computer.",
                "GMP Uninstalled",
                0x40,
            )
            sys.exit(0)
