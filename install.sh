#!/usr/bin/env bash
# Installs the Force Stop Cinnamon extension (force-stop@enisn) for the current user.
set -e

SRC="$(cd "$(dirname "$0")" && pwd)/force-stop@enisn"
DEST="$HOME/.local/share/cinnamon/extensions/force-stop@enisn"

if [ ! -f "$SRC/extension.js" ] || [ ! -f "$SRC/metadata.json" ]; then
    echo "Error: force-stop@enisn extension files not found next to install.sh" >&2
    exit 1
fi

mkdir -p "$HOME/.local/share/cinnamon/extensions"
rm -rf "$DEST"
cp -r "$SRC" "$DEST"

echo "Installed Force Stop to $DEST"
echo
echo "Next: open System Settings -> Extensions, find 'Force Stop', and enable it."
