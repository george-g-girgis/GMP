"""
uninstall.py — Uninstaller for GMP (Glass Media Player).

Triggered when uninstalled from Windows Settings 'Installed Apps' or
Control Panel 'Programs and Features', or run manually.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.installer import uninstall


def main():
    is_silent = "--silent" in sys.argv or "-s" in sys.argv

    if is_silent:
        uninstall(remove_user_data=True)
        sys.exit(0)

    try:
        from PyQt6.QtWidgets import QApplication, QMessageBox

        app = QApplication(sys.argv)

        reply = QMessageBox.question(
            None,
            "Uninstall GMP",
            "Are you sure you want to completely remove GMP (Glass Media Player)?\n\n"
            "This will remove the Start Menu shortcut, startup registration, and settings.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            uninstall(remove_user_data=True)
            QMessageBox.information(
                None,
                "GMP Uninstalled",
                "GMP (Glass Media Player) was successfully removed from your computer.",
            )
    except Exception:
        # Fallback to console / ctypes messagebox if PyQt6 is not available
        import ctypes
        MB_YESNO = 4
        MB_ICONQUESTION = 0x20
        IDYES = 6
        res = ctypes.windll.user32.MessageBoxW(
            0,
            "Are you sure you want to completely remove GMP (Glass Media Player)?",
            "Uninstall GMP",
            MB_YESNO | MB_ICONQUESTION,
        )
        if res == IDYES:
            uninstall(remove_user_data=True)
            ctypes.windll.user32.MessageBoxW(
                0,
                "GMP was successfully removed from your computer.",
                "GMP Uninstalled",
                0x40,
            )


if __name__ == "__main__":
    main()
