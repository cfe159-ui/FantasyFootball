#!/usr/bin/env python
"""Build a double-clickable macOS .app bundle for the agent.

Produces a minimal bundle whose executable launches the project's own venv,
so the app always runs the code in this directory -- edit the source and the
next launch picks it up, no rebuild needed.
"""
from __future__ import annotations

import os
import plistlib
import shutil
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_NAME = "Fantasy Agent"
BUNDLE_ID = "local.ffagent.desktop"


def build(dest_dir: Path) -> Path:
    app = dest_dir / f"{APP_NAME}.app"
    macos = app / "Contents" / "MacOS"
    resources = app / "Contents" / "Resources"
    if app.exists():
        shutil.rmtree(app)
    macos.mkdir(parents=True)
    resources.mkdir(parents=True)

    python = ROOT / ".venv" / "bin" / "python"
    launcher = macos / "FantasyAgent"
    launcher.write_text(
        "#!/bin/sh\n"
        f'cd "{ROOT}" || exit 1\n'
        f'exec "{python}" -c "'
        "import sys; sys.path.insert(0, \\\"" + str(ROOT) + "\\\"); "
        "import ff; from ff.web.app import run_window; run_window()"
        '"\n'
    )
    launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    info = {
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleVersion": "0.1.0",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleExecutable": "FantasyAgent",
        "CFBundlePackageType": "APPL",
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
        # A GUI app, not a background agent: show it in the Dock.
        "LSUIElement": False,
    }
    with (app / "Contents" / "Info.plist").open("wb") as fh:
        plistlib.dump(info, fh)

    return app


if __name__ == "__main__":
    target = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else ROOT / "dist"
    target.mkdir(parents=True, exist_ok=True)
    built = build(target)
    print(f"Built {built}")
    print("Double-click it, or drag it into /Applications.")
