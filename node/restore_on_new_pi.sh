#!/bin/bash
set -e

PROJECT_DIR="$HOME/StockHive/node"

echo "=== StockHive node restore ==="

# 1) System deps (minimal set; adjust if you add more later)
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip git

# 2) Python venv + deps
cd "$PROJECT_DIR"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate

echo
echo "Restore complete."
echo "Next:"
echo "  1) Put your inventory.db back in $PROJECT_DIR (from USB backup)"
echo "  2) If using systemd, install the service:"
echo "       sudo cp ../systemd/stockpi-node.service /etc/systemd/system/stockpi-node.service"
echo "       sudo systemctl daemon-reload"
echo "       sudo systemctl enable --now stockpi-node"
echo
