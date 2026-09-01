#!/usr/bin/env bash
# =============================================================================
# StockHive Launcher Setup Script
# Installs this device as the StockHive launcher: discovers StockPi nodes
# over mDNS and shows them as a tile grid, plus the weather widget.
# Run this on ONE device only (its own Pi, recommended — not on a node).
#
# Run from the root of the cloned repo:
#   chmod +x setup-launcher.sh && sudo ./setup-launcher.sh
# =============================================================================

set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

if [[ $EUID -ne 0 ]]; then
  error "Please run with sudo: sudo ./setup-launcher.sh"
fi

REAL_USER="${SUDO_USER:-$(logname 2>/dev/null || echo pi)}"
REAL_HOME=$(getent passwd "$REAL_USER" | cut -d: -f6)

echo ""
echo -e "${BOLD}============================================${NC}"
echo -e "${BOLD}   StockHive Launcher Setup${NC}"
echo -e "${BOLD}============================================${NC}"
echo ""

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCHER_DIR="$REPO_DIR/launcher"
info "Repo directory : $REPO_DIR"
info "Installing for user: $REAL_USER (home: $REAL_HOME)"
echo ""

# =============================================================================
# 1. COLLECT CONFIGURATION
# =============================================================================
echo -e "${BOLD}--- Configuration ---${NC}"
while true; do
  read -rp "Enter ZIP code for the weather widget (5 digits): " ZIP_CODE
  if [[ "$ZIP_CODE" =~ ^[0-9]{5}$ ]]; then
    break
  fi
  warn "Please enter a valid 5-digit ZIP code."
done
echo ""

# =============================================================================
# 2. INSTALL SYSTEM PACKAGES
# =============================================================================
echo -e "${BOLD}--- Installing system packages ---${NC}"
apt-get update -qq
apt-get install -y python3 python3-pip python3-venv nginx > /dev/null 2>&1
success "System packages installed."

# =============================================================================
# 3. CREATE config.json (if it doesn't exist)
# =============================================================================
echo -e "${BOLD}--- Creating config.json ---${NC}"
CONFIG_PATH="$LAUNCHER_DIR/config.json"
if [[ -f "$CONFIG_PATH" ]]; then
  warn "config.json already exists — skipping. Edit it manually if needed."
else
  cat > "$CONFIG_PATH" <<EOF
{
  "weather": {
    "zip": "$ZIP_CODE"
  }
}
EOF
  chown "$REAL_USER":"$REAL_USER" "$CONFIG_PATH"
  success "config.json created with ZIP $ZIP_CODE."
fi

CACHE_DIR="$LAUNCHER_DIR/data_cache"
mkdir -p "$CACHE_DIR"
chown -R "$REAL_USER":"$REAL_USER" "$CACHE_DIR"

# =============================================================================
# 4. PYTHON VIRTUAL ENVIRONMENT + DEPENDENCIES
# =============================================================================
echo -e "${BOLD}--- Setting up Python virtual environment ---${NC}"
VENV_DIR="$LAUNCHER_DIR/venv"

if [[ -d "$VENV_DIR" ]]; then
  warn "venv already exists — skipping creation."
else
  sudo -u "$REAL_USER" python3 -m venv "$VENV_DIR"
  success "venv created."
fi

info "Installing Python dependencies..."
sudo -u "$REAL_USER" "$VENV_DIR/bin/pip" install --upgrade pip --quiet
sudo -u "$REAL_USER" "$VENV_DIR/bin/pip" install -r "$LAUNCHER_DIR/requirements.txt" --quiet
success "Python dependencies installed."

# =============================================================================
# 5. NGINX
# =============================================================================
echo -e "${BOLD}--- Configuring nginx ---${NC}"
cp "$REPO_DIR/nginx/launcher.conf" /etc/nginx/sites-available/stockpi-launcher.conf
ln -sf /etc/nginx/sites-available/stockpi-launcher.conf /etc/nginx/sites-enabled/stockpi-launcher.conf
rm -f /etc/nginx/sites-enabled/default
nginx -t > /dev/null 2>&1 && systemctl restart nginx
success "nginx configured and restarted."

# =============================================================================
# 6. SYSTEMD SERVICE
# =============================================================================
echo -e "${BOLD}--- Installing systemd service ---${NC}"
SERVICE_DEST="/etc/systemd/system/stockpi-launcher.service"
sed \
  -e "s|User=kinv|User=$REAL_USER|g" \
  -e "s|/home/kinv/launcher|$LAUNCHER_DIR|g" \
  "$REPO_DIR/systemd/stockpi-launcher.service" > "$SERVICE_DEST"

systemctl daemon-reload
systemctl enable stockpi-launcher.service
systemctl restart stockpi-launcher.service
success "stockpi-launcher.service installed and started."

# =============================================================================
# SUDOERS
# =============================================================================
SUDOERS_FILE="/etc/sudoers.d/stockpi-launcher"
cat > "$SUDOERS_FILE" <<EOF
$REAL_USER ALL=(ALL) NOPASSWD: /bin/systemctl restart stockpi-launcher.service
EOF
chmod 0440 "$SUDOERS_FILE"
success "Sudoers entry added for $REAL_USER (service restart)."

# =============================================================================
# DONE
# =============================================================================
echo ""
echo -e "${BOLD}============================================${NC}"
echo -e "${GREEN}${BOLD}   Setup complete!${NC}"
echo -e "${BOLD}============================================${NC}"
echo ""

LOCAL_IP=$(hostname -I | awk '{print $1}')
echo -e "  Open in a browser: ${CYAN}http://${LOCAL_IP}${NC}"
echo -e "  StockPi nodes on the same network should appear automatically."
echo -e "  Manage nodes / change ZIP code: ${CYAN}http://${LOCAL_IP}/settings${NC}"
echo ""
