#!/bin/bash
# Build logos_<version>_all.deb from current source.
# Produces the .deb at the project root.
set -e

cd "$(dirname "$0")"

VERSION=$(grep -oP 'VERSION\s*=\s*"\K[^"]+' backend/version.py)
PKG="logos"
ARCH="all"
STAGE="build/${PKG}_${VERSION}_${ARCH}"

echo "==> Building ${PKG} ${VERSION}"
rm -rf "$STAGE"
mkdir -p "$STAGE"

# ── App code (Python + frontend) → /usr/share/logos/
install -d "$STAGE/usr/share/logos"
cp app.py "$STAGE/usr/share/logos/"
cp -r backend "$STAGE/usr/share/logos/"
cp -r frontend "$STAGE/usr/share/logos/"

# Drop build artefacts that may sneak in from local dev
find "$STAGE/usr/share/logos" -type d \( -name __pycache__ -o -name dist -o -name build \) \
    -exec rm -rf {} + 2>/dev/null || true
find "$STAGE/usr/share/logos" -name '*.pyc' -delete 2>/dev/null || true
find "$STAGE/usr/share/logos" -name '*.spec' -delete 2>/dev/null || true

# ── Launcher wrapper → /usr/bin/logos
install -d "$STAGE/usr/bin"
cat > "$STAGE/usr/bin/logos" <<'EOF'
#!/bin/bash
exec /usr/bin/python3 /usr/share/logos/app.py "$@"
EOF
chmod 755 "$STAGE/usr/bin/logos"

# ── Desktop entry → /usr/share/applications/
install -d "$STAGE/usr/share/applications"
cp logos.desktop "$STAGE/usr/share/applications/"

# ── Icons → /usr/share/icons/hicolor/<size>/apps/logos.png
for sz in 16 22 32 48 64 128 256 512; do
    install -d "$STAGE/usr/share/icons/hicolor/${sz}x${sz}/apps"
    cp "icons/logos-${sz}.png" "$STAGE/usr/share/icons/hicolor/${sz}x${sz}/apps/logos.png"
done

# ── Scalable SVG
install -d "$STAGE/usr/share/icons/hicolor/scalable/apps"
cp icon.svg "$STAGE/usr/share/icons/hicolor/scalable/apps/logos.svg"

# ── Doc → /usr/share/doc/logos/
install -d "$STAGE/usr/share/doc/logos"
cp README.md developers.md "$STAGE/usr/share/doc/logos/"

# ── DEBIAN control
install -d "$STAGE/DEBIAN"
cat > "$STAGE/DEBIAN/control" <<EOF
Package: ${PKG}
Version: ${VERSION}
Section: net
Priority: optional
Architecture: ${ARCH}
Depends: python3 (>= 3.10), python3-pip, python3-gi, gir1.2-webkit2-4.1, libwebkit2gtk-4.1-0, libgtk-3-0
Recommends: ollama
Maintainer: piggypop <noreply@piggypop.net>
Homepage: https://github.com/piggypop/logos
Description: Minimal desktop chat client for local LLMs via Ollama
 Logos is a small desktop chat app for talking with local LLMs through
 Ollama. It bundles real web search (via your local SearXNG), URL and
 YouTube transcript reading, file attachments (text/PDF/DOCX, plus
 images when the model supports vision), and persistent cross-chat
 memory. Vanilla HTML/JS frontend, Flask backend, native window via
 pywebview (GTK+WebKit2). No accounts, no cloud, no telemetry.
EOF

# ── postinst: pip-install the Python packages that aren't packaged by Debian
cat > "$STAGE/DEBIAN/postinst" <<'EOF'
#!/bin/bash
set -e

if [ "$1" = "configure" ]; then
    /usr/bin/python3 -m pip install --break-system-packages --quiet --upgrade \
        flask flask-cors ollama httpx pywebview trafilatura youtube-transcript-api \
        pypdf python-docx || {
        echo ""
        echo "WARNING: pip install failed. Logos may not start until you run:" >&2
        echo "  sudo pip3 install --break-system-packages -r /usr/share/logos/backend/requirements.txt" >&2
    }

    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database -q /usr/share/applications 2>/dev/null || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -q -f /usr/share/icons/hicolor 2>/dev/null || true
    fi
fi

exit 0
EOF
chmod 755 "$STAGE/DEBIAN/postinst"

# ── prerm: nothing to do; keep user data in ~/.config/logos & ~/.local/share/logos
cat > "$STAGE/DEBIAN/prerm" <<'EOF'
#!/bin/bash
exit 0
EOF
chmod 755 "$STAGE/DEBIAN/prerm"

# ── Build
echo "==> Packaging..."
dpkg-deb --root-owner-group --build "$STAGE" "${PKG}_${VERSION}_${ARCH}.deb"

echo ""
echo "==> Done: $(pwd)/${PKG}_${VERSION}_${ARCH}.deb"
echo "    Install: sudo apt install ./${PKG}_${VERSION}_${ARCH}.deb"
