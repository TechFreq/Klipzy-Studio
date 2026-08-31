#!/usr/bin/env bash
# Klipzy Studio - macOS one-click launcher.
# Double-click this file in Finder to start the app.
# It simply runs the maintained macOS launcher in scripts/run_macos.sh.

# Move to this file's folder (the repo root) so paths resolve correctly.
cd "$(dirname "$0")"

# Hand off to the real launcher.
bash scripts/run_macos.sh

# Keep the Terminal window open if something goes wrong, so errors are readable.
status=$?
if [ $status -ne 0 ]; then
    echo ""
    echo "Klipzy Studio exited with an error (code $status)."
    echo "Press Enter to close this window."
    read -r _
fi
