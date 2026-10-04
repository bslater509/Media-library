#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Load local environment configuration if present
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    . "$SCRIPT_DIR/.env"
    set +a
fi

PORT="${PORT:-8888}"
HOST="${HOST:-0.0.0.0}"
VENV_DIR="$SCRIPT_DIR/venv"

# Create venv if missing
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install --upgrade pip
    "$VENV_DIR/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"
fi

# Detect primary LAN IP
LAN_IP=$(ip route get 1.1.1.1 2>/dev/null | awk '{print $7; exit}' || hostname -I | awk '{print $1}')
if [ -z "$LAN_IP" ]; then
    LAN_IP="127.0.0.1"
fi

DEST_DISPLAY="${LIBRARY_DIR:-./data/media}"

echo "================================================================="
echo "  📁 Media Library & Video Sorter is Starting"
echo "  Media Library:       http://$LAN_IP:$PORT"
echo "  Incoming Sorter:     http://$LAN_IP:$PORT/sorter"
echo "  Local Machine:       http://localhost:$PORT"
echo "  Destination Dir:     $DEST_DISPLAY"
echo "================================================================="
echo "Press Ctrl+C to stop the server."
echo ""

exec "$VENV_DIR/bin/python" "$SCRIPT_DIR/app.py" --host "$HOST" --port "$PORT" "$@"
