"""SparkGap Console: Qt Quick client for one or more SparkGap feeds.

usage: sparkgap_console.py [--gpu] [ws://host:port/feed[#token]] ...

Servers are kept in a list edited in Settings; any given here are added to it.

Draws with Qt Quick's software renderer by default: about a sixth of the CPU of OpenGL without a
GPU (remote desktop, 17% against 111% measured) and well under half the memory. --gpu uses the
graphics card instead.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# importing backend registers Waterfall for QML
from backend import (
    Console,
    feed_url,
    load_feeds,
    merge_feeds,
    normalize_code,
    save_feeds,
    url_parts,
)
from PySide6.QtCore import QSettings
from PySide6.QtGui import QFontDatabase, QGuiApplication, QIcon
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow, QSGRendererInterface

# next to this file, or inside the bundle when built into SparkGapConsole.exe (PyInstaller)
HERE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
# fonts and icons are shared with the web console
ASSETS = HERE if hasattr(sys, "_MEIPASS") else HERE.parent / "web"


def build_id() -> str:
    """Commit the console was built from: build.txt in a packaged build, else git."""
    stamp = HERE / "build.txt"
    if stamp.exists():
        return stamp.read_text().strip()
    try:
        out = subprocess.run(
            ["git", "describe", "--always", "--dirty"],
            cwd=HERE, capture_output=True, text=True, timeout=5,
        )
    except OSError:
        return "unknown"
    return out.stdout.strip() or "unknown"


def parse(args: list[str]) -> list[tuple[str, str]]:
    feeds = []
    for a in args:
        url, _, token = a.partition("#")
        if "://" not in url:
            url = f"ws://{url}"
        if url.startswith("ws://"):
            url = feed_url(*url_parts(url))
        elif not url.rstrip("/").endswith("/feed"):
            url = url.rstrip("/") + "/feed"
        feeds.append((url, normalize_code(token)))
    return feeds


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--gpu"]
    if "--gpu" not in sys.argv:
        QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.Software)
    app = QGuiApplication(sys.argv)
    app.setApplicationName("SparkGap Console")
    app.setOrganizationName("SparkGap")
    for f in (ASSETS / "fonts").glob("*.ttf"):
        QFontDatabase.addApplicationFont(str(f))
    store = QSettings()
    feeds = merge_feeds(load_feeds(store), parse(args))
    save_feeds(store, feeds)
    console = Console(feeds, store)
    # no saved servers yet: start in demo
    console.setDemo(str(store.value("ui/demo", not feeds)).lower() == "true")
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("console_", console)
    engine.rootContext().setContextProperty("build_", build_id())
    engine.load(HERE / "qml" / "Main.qml")
    if not engine.rootObjects():
        return 1
    icon = QIcon()
    for f in sorted((ASSETS / "icons").glob("sparkgap-*.png")):
        icon.addFile(str(f))
    app.setWindowIcon(icon)
    rc = app.exec()
    del engine  # QML goes before the objects it binds to
    return rc


if __name__ == "__main__":
    sys.exit(main())
