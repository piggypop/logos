#!/bin/bash
set -e

cd "$(dirname "$0")"

echo "==> Installing build deps..."
pip install pyinstaller --break-system-packages -q
pip install -r backend/requirements.txt --break-system-packages -q

echo "==> Building single-binary app..."
rm -rf build dist *.spec
pyinstaller --onefile --windowed \
    --name chat \
    --add-data "frontend:frontend" \
    --paths backend \
    --collect-all webview \
    app.py

echo ""
echo "==> Done. Binary: $(pwd)/dist/chat"
echo ""
echo "To register in the application menu:"
echo "  cp chat.desktop ~/.local/share/applications/"
