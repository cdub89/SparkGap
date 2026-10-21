#!/usr/bin/env bash
# SparkGap Console for Windows, built on Linux (PyInstaller can't build for Windows here):
# the official Windows embeddable Python, the Windows wheels of the requirements at the versions
# the Linux build uses, the console's files in the repo's layout (console/ and web/fonts, web/icons,
# so it finds them as it does from source), and SparkGapConsole.exe, pip's own GUI launcher, which
# starts it with no console window. Modelled on rbn-node-validation's packaging/windows_zip.sh.
# Usage, from console/ after ./build has made .venv:  ./windows_zip.sh <out dir> <name tag>
set -euo pipefail
OUT=$(readlink -f "$1"); NAME=$2
HERE=$(dirname "$(readlink -f "$0")")
# progress on stdout, one line a step; tool output on stderr (build sends it to build.log)
say() { echo "     Windows   $*"; }
PYVER=3.12.10  # the last 3.12 with a Windows embeddable build
PYSHA=4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3  # python.org's SPDX record of the zip
WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT
APP=$WORK/SparkGapConsole
SITE=$APP/_internal/Lib/site-packages

mkdir -p "$APP/_internal"
curl -fsSL -A "sparkgap-console-build" -o "$WORK/py.zip" \
  "https://www.python.org/ftp/python/$PYVER/python-$PYVER-embed-amd64.zip"
echo "$PYSHA  $WORK/py.zip" | sha256sum -c --quiet - >&2
unzip -q "$WORK/py.zip" -d "$APP/_internal"
# the embeddable Python only searches what its ._pth lists: add site-packages
printf 'python312.zip\r\n.\r\nLib\\site-packages\r\nimport site\r\n' > "$APP/_internal/python312._pth"
say "Python $PYVER embeddable, downloaded and checked"

# the versions the Linux build uses, so both apps carry the same Qt
PINS=$("$HERE/.venv/bin/python" -c 'from importlib.metadata import version as v
print(" ".join(f"{p}=={v(p)}" for p in ("PySide6-Essentials", "PySide6-Addons", "numpy")))')
# shellcheck disable=SC2086
uv pip install -q --target "$SITE" --python-platform x86_64-pc-windows-msvc --python-version 3.12 $PINS >&2
BEFORE=$(du -sm "$APP" | cut -f1)
say "installed for Windows: $PINS"

# leave out the Qt parts the console never loads: the spec's DROP list, inside PySide6 only
python3 - "$SITE" "$HERE/SparkGapConsole.spec" <<'EOF'
import ast, re, shutil, sys
from pathlib import Path
site, spec = Path(sys.argv[1]), Path(sys.argv[2]).read_text(encoding="utf-8")
drop = re.compile("|".join(ast.literal_eval(re.search(r"^DROP = (\[.*?\])$", spec, re.S | re.M).group(1))))
extra = re.compile(r"^PySide6/(include|typesystems|glue|scripts|support/generate_pyi)|^PySide6/[^/]+\.exe$|\.pyi$")
for p in sorted(site.rglob("*"), reverse=True):
    rel = p.relative_to(site).as_posix()
    if p.is_file() and rel.startswith("PySide6/") and (drop.search("/" + rel) or extra.search(rel)):
        p.unlink()
    elif p.is_dir() and not any(p.iterdir()):
        p.rmdir()
for d in site.glob("bin"):  # console scripts point at a Linux path
    shutil.rmtree(d)
EOF
say "unused Qt parts removed: $BEFORE MB to $(du -sm "$APP" | cut -f1) MB"

mkdir -p "$APP/_internal/app/console" "$APP/_internal/app/web"
cp "$HERE"/sparkgap_console.py "$HERE"/backend.py "$HERE"/build.txt "$APP/_internal/app/console/"
cp -r "$HERE/qml" "$APP/_internal/app/console/"
cp -r "$HERE/../web/fonts" "$HERE/../web/icons" "$APP/_internal/app/web/"
say "console files added (build $(cat "$HERE/build.txt"))"

uv pip install -q --target "$WORK/distlib" "distlib==0.4.4" >&2  # carries pip's Windows launchers
python3 "$HERE/win_launcher.py" "$WORK/distlib/distlib/w64.exe" "$APP/SparkGapConsole.exe"
say "SparkGapConsole.exe launcher added (distlib 0.4.4 w64.exe)"

ZIP="$OUT/SparkGapConsole-$NAME-windows-x64.zip"
rm -f "$ZIP"
(cd "$WORK" && zip -qr -9 "$ZIP" SparkGapConsole)
