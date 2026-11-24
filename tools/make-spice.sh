#!/usr/bin/env bash
# Assembles the folder that linuxmint/cinnamon-spices-extensions expects in dist/:
#   force-stop@enisn/info.json, screenshot.png, README.md and files/force-stop@enisn/
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UUID=force-stop@enisn
DEST="$ROOT/dist/$UUID"

if [ ! -f "$ROOT/$UUID/extension.js" ] || [ ! -f "$ROOT/screenshots/menu.png" ]; then
    echo "Error: the extension or screenshots/menu.png is missing. Run python3 tests/nested_test.py first." >&2
    exit 1
fi

rm -rf "$DEST"
mkdir -p "$DEST/files"
cp -r "$ROOT/$UUID" "$DEST/files/"
cp "$ROOT/README.md" "$DEST/"
cp -r "$ROOT/screenshots" "$DEST/screenshots"
cp "$ROOT/screenshots/menu.png" "$DEST/screenshot.png"
printf '{\n    "author": "enisn",\n    "license": "MIT"\n}\n' > "$DEST/info.json"

echo "Spice assembled in $DEST"
echo "Check it with the validate-spice script of the Spices repository:"
echo "  cd dist && python3 /path/to/cinnamon-spices-extensions/validate-spice $UUID"
