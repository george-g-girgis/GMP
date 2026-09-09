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

from core.installer import run_uninstaller_gui_or_cli


def main():
    run_uninstaller_gui_or_cli()


if __name__ == "__main__":
    main()
