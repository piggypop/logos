#!/bin/bash
# Deploy Logos changes from source repo to installed location
# Usage: ./deploy.sh
set -e

SRC="/home/piggypop/Projects/Logos"
DST="/usr/share/logos"

echo "==> Deploying Logos from source to $DST"

# Copy main app
sudo cp "$SRC/app.py" "$DST/app.py"

# Copy backend
sudo cp "$SRC/backend/"*.py "$DST/backend/"

# Copy frontend
sudo cp "$SRC/frontend/"* "$DST/frontend/"

# Clean up __pycache__ in installed location
sudo find "$DST" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

echo "==> Files synced. Restart Logos to apply changes."
echo "    You can run: kill \$(pgrep -f 'python3 /usr/share/logos/app.py') && logos &"