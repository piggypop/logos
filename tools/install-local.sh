#!/bin/bash
#
# install-local.sh — Sync the dev tree into the installed /usr/share/logos
#
# Use this when you want to test in-progress changes against the real
# installed Logos app without rebuilding the .deb. It copies only source
# files that the running app actually reads — no fonts, no icons, no
# requirements.txt (those rarely change and are managed by the .deb).
#
# Usage:
#   sudo ./tools/install-local.sh
#
# After running, fully exit Logos (System tray → Quit, or settings → Quit
# Logos) and re-launch from the application menu. A simple window-close
# only minimises and won't pick up the new code.

set -euo pipefail

DEV_DIR="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL_DIR="/usr/share/logos"

if [[ $EUID -ne 0 ]]; then
    echo "This script must be run as root (the install dir is owned by root)." >&2
    echo "Re-run with: sudo $0" >&2
    exit 1
fi

if [[ ! -d "$INSTALL_DIR" ]]; then
    echo "Install dir not found: $INSTALL_DIR" >&2
    echo "Is Logos installed via the .deb package?" >&2
    exit 1
fi

# Files we actually touched during dev iteration. Add new ones here as
# the codebase grows. We deliberately list files explicitly instead of
# rsync'ing the whole tree, so a stale dev artifact can't sneak in.
FILES=(
    "app.py"
    "backend/config.py"
    "backend/notes.py"
    "backend/obsidian_sync.py"
    "backend/prompts.py"
    "backend/search_providers.py"
    "backend/server.py"
    "backend/tool_router.py"
    "backend/version.py"
    "frontend/app.js"
    "frontend/index.html"
    "frontend/style.css"
)

echo "==> Source: $DEV_DIR"
echo "==> Target: $INSTALL_DIR"
echo

COPIED=0
SKIPPED=0
for f in "${FILES[@]}"; do
    src="$DEV_DIR/$f"
    dst="$INSTALL_DIR/$f"

    if [[ ! -f "$src" ]]; then
        echo "  -- missing in dev:  $f  (skipping)"
        continue
    fi

    if [[ ! -f "$dst" ]]; then
        echo "  ?? missing in install: $f  (copying anyway)"
    fi

    if cmp -s "$src" "$dst" 2>/dev/null; then
        echo "  == same:           $f"
        SKIPPED=$((SKIPPED + 1))
        continue
    fi

    # Preserve mode (644) and ownership (root:root) of the original install.
    install -m 0644 -o root -g root "$src" "$dst"
    echo "  ++ updated:        $f"
    COPIED=$((COPIED + 1))
done

# ── Sync non-source asset directories ──────────────────────────────
#
# The v1.5.0 .deb shipped without the icons/ directory in /usr/share/logos.
# build_tray() reads icons/logos-32.png from BASE_DIR — without it the tray
# is silently disabled. We patch the install on every script run so this
# can't drift again until 1.6.0 fixes the .deb packaging.
ASSET_DIRS=(
    "icons"
    "backend/fonts"
)
echo
for d in "${ASSET_DIRS[@]}"; do
    src_dir="$DEV_DIR/$d"
    dst_dir="$INSTALL_DIR/$d"
    if [[ ! -d "$src_dir" ]]; then
        echo "  -- missing in dev dir: $d  (skipping)"
        continue
    fi
    if [[ ! -d "$dst_dir" ]]; then
        echo "  ?? creating missing install dir: $d"
        install -d -m 0755 -o root -g root "$dst_dir"
    fi
    # Mirror the directory contents. -a preserves attrs, --delete-after would
    # be too aggressive; we just copy in. Use cp -u to skip unchanged files.
    cp -u "$src_dir"/* "$dst_dir/" 2>/dev/null && echo "  ++ synced dir:     $d/" || echo "  -- nothing to sync in $d/"
    chown -R root:root "$dst_dir"
    chmod 644 "$dst_dir"/* 2>/dev/null || true
done

echo
echo "==> Clearing stale __pycache__ in install..."
find "$INSTALL_DIR" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

# ── Harden /usr/bin/logos with NVIDIA env vars ─────────────────────
#
# The white-window NVIDIA bug (see comment at top of app.py) bites when
# the user launches Logos via anything that bypasses the .desktop wrapper
# — terminal, scripts, `logos &`, etc. The dev app.py sets the env vars
# itself via os.environ.setdefault, but only after the .deb publishes
# that version. Until then, we patch the shim wrapper so every launch
# path inherits the right env. Idempotent: writes only if the wrapper
# doesn't already export the vars.
WRAPPER=/usr/bin/logos
if [[ -f "$WRAPPER" ]] && ! grep -q WEBKIT_DISABLE_DMABUF_RENDERER "$WRAPPER"; then
    echo
    echo "==> Hardening $WRAPPER with NVIDIA env vars..."
    cat > "$WRAPPER" <<'EOF'
#!/bin/bash
# Set WebKit2GTK/NVIDIA rendering mitigations for every launch path
# (terminal, .desktop, scripts). See app.py header comment for the why.
exec env \
    WEBKIT_DISABLE_DMABUF_RENDERER=1 \
    WEBKIT_DISABLE_COMPOSITING_MODE=1 \
    GSK_RENDERER=cairo \
    LIBGL_ALWAYS_SOFTWARE=1 \
    /usr/bin/python3 /usr/share/logos/app.py "$@"
EOF
    chmod 0755 "$WRAPPER"
    chown root:root "$WRAPPER"
    echo "  ++ wrapper patched (idempotent — re-runs are no-ops)"
fi

# ── Verify runtime Python deps ─────────────────────────────────────
#
# The .deb does not install Python packages from backend/requirements.txt.
# We probe the system interpreter for the runtime-critical ones and warn
# if any are missing — same root cause as the icons/ gap, see M4 in
# roadmap-v1.6.md.
echo
echo "==> Checking Python deps in /usr/bin/python3..."
MISSING_DEPS=()
for pkg in fpdf pystray PIL flask ollama webview trafilatura; do
    if ! /usr/bin/python3 -c "import $pkg" 2>/dev/null; then
        MISSING_DEPS+=("$pkg")
    fi
done
if [[ ${#MISSING_DEPS[@]} -gt 0 ]]; then
    echo "  !! missing: ${MISSING_DEPS[*]}"
    # Map import names back to pip package names for the suggested command.
    declare -A PIP_NAMES=(
        [fpdf]="fpdf2"
        [pystray]="pystray"
        [PIL]="Pillow"
        [flask]="Flask"
        [ollama]="ollama"
        [webview]="pywebview"
        [trafilatura]="trafilatura"
    )
    PIP_LIST=""
    for d in "${MISSING_DEPS[@]}"; do
        PIP_LIST+=" ${PIP_NAMES[$d]}"
    done
    echo "  -> fix with:"
    echo "     sudo /usr/bin/python3 -m pip install --break-system-packages$PIP_LIST"
else
    echo "  OK — all runtime deps importable."
fi

echo
echo "==> Done. Updated $COPIED file(s), skipped $SKIPPED unchanged."
echo
echo "Next step: fully QUIT Logos (system tray → Quit, NOT just close the window),"
echo "then re-launch it from the application menu so the new code loads."
