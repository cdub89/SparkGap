# PyInstaller build of the console: one folder with SparkGapConsole.exe, QML and fonts inside.
# Build on Windows from this folder:  .venv\Scripts\pyinstaller --noconfirm SparkGapConsole.spec
import os
import re

a = Analysis(
    ["sparkgap_console.py"],
    datas=[("qml", "qml"), ("../web/fonts", "fonts"), ("../web/icons", "icons")] + ([("build.txt", ".")] if os.path.exists("build.txt") else []),
    hiddenimports=["backend", "PySide6.QtWebSockets", "PySide6.QtQuick", "PySide6.QtQml"],
    excludes=["tkinter", "PySide6.QtWidgets", "PySide6.QtWebEngineCore", "PySide6.Qt3DCore",
              "PySide6.QtMultimedia", "PySide6.QtPdf", "PySide6.QtCharts"],
)

# Qt parts the console never loads (it uses QtQuick, Controls Basic, Layouts, WebSockets and the
# software renderer). Dropping them takes the folder from 238 MB to about half.
DROP = [r"3D", r"Physics", r"Graphs", r"Charts", r"DataVisualization", r"Location", r"Positioning",
        r"Multimedia", r"Pdf", r"WebEngine", r"WebView", r"WebChannel", r"Sensors", r"TextToSpeech",
        r"Qt6Test|/QtTest", r"Labs|/labs/", r"Imagine", r"FluentWinUI3", r"Material", r"Universal",
        r"Designer", r"Help", r"Scxml", r"StateMachine", r"RemoteObjects", r"SerialPort", r"Bluetooth",
        r"Nfc", r"Sql", r"VirtualKeyboard", r"ShaderTools", r"Qt5Compat", r"Qt6Widgets",
        r"QuickDialogs|/Dialogs", r"Particles", r"Timeline", r"Lottie", r"SpatialAudio", r"Scene2D",
        r"opengl32sw", r"/translations/", r"/qmltooling/", r"QuickEffects|/Effects",
        r"Controls/(Fusion|iOS|macOS|Windows|NativeStyle)", r"QtWebSockets/"]
_drop = re.compile("|".join(DROP))
a.binaries = [b for b in a.binaries if not _drop.search(b[0].replace("\\", "/"))]
a.datas = [d for d in a.datas if not _drop.search(d[0].replace("\\", "/"))]

pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="SparkGapConsole", console=False,
          icon="../web/icons/sparkgap.ico")
coll = COLLECT(exe, a.binaries, a.datas, name="SparkGapConsole")
