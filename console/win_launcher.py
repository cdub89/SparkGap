"""Make SparkGapConsole.exe: pip's GUI launcher (distlib's w64.exe) with a start script appended.

The launcher runs the appended script with the Python its shebang names; <launcher_dir> makes that
path relative to the launcher's own folder, so the console runs from wherever it's unzipped.
Modelled on rbn-node-validation's packaging/win_launcher.py.
Usage: python3 win_launcher.py <w64.exe> <out .exe>
"""

import io
import sys
import zipfile

MAIN = b'''import ctypes, os, runpy, sys
from pathlib import Path
# our own app ID: the taskbar then shows the window's icon, not pythonw.exe's
ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SparkGap.Console")
# a windowed app has nowhere to show errors: keep them in console.log
log = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "SparkGapConsole" / "console.log"
log.parent.mkdir(parents=True, exist_ok=True)
sys.stdout = sys.stderr = open(log, "w", encoding="utf-8", buffering=1)
# the console's files sit in the repo's layout under _internal/app, next to this Python
app = Path(sys.executable).resolve().parent / "app" / "console"
sys.path.insert(0, str(app))
sys.argv[0] = str(app / "sparkgap_console.py")
runpy.run_path(sys.argv[0], run_name="__main__")
'''

launcher, out = sys.argv[1], sys.argv[2]
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z:
    z.writestr("__main__.py", MAIN)
with open(launcher, "rb") as f, open(out, "wb") as o:
    o.write(f.read() + b"#!<launcher_dir>\\_internal\\pythonw.exe\r\n" + buf.getvalue())
