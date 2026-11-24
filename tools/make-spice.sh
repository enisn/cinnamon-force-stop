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
printf '{\n    "author": "enisn",\n    "license": "MIT"\n}\n' > "$DEST/info.json"
cp "$ROOT/screenshots/menu.png" "$DEST/screenshot.png"

# The installer and the test suite live in this repository only, so they stay out of the
# README that ships with the spice.
awk '
    /^## / { skip = ($0 == "## Install" || $0 == "## Tests") }
    /^## License/ && !added {
        print "## Source"
        print ""
        print "The source repository, with the tests, is https://github.com/enisn/cinnamon-force-stop."
        print ""
        added = 1
    }
    !skip { print }
' "$ROOT/README.md" > "$DEST/README.md"

# Only the screenshots the README shows are shipped.
grep -o 'screenshots/[A-Za-z0-9._-]*' "$DEST/README.md" | sort -u | while read -r image; do
    mkdir -p "$DEST/$(dirname "$image")"
    cp "$ROOT/$image" "$DEST/$image"
done

echo "Spice assembled in $DEST"
echo "Check it with the validate-spice script of the Spices repository:"
echo "  cd dist && python3 /path/to/cinnamon-spices-extensions/validate-spice $UUID"
