#!/bin/bash
echo "===================================="
echo "  🎬 FFmpeg Media Compressor v1.0"
echo "===================================="
echo ""

cd "$(dirname "$0")"

echo "[1/2] Checking Python..."
python3 --version >/dev/null 2>&1 || { echo "❌ Python not found."; exit 1; }

echo "[2/2] Installing dependencies..."
pip3 install -r requirements.txt -q

echo ""
echo "===================================="
echo "  🚀 Starting server..."
echo "===================================="
python3 -m backend.main
