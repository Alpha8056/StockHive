#!/usr/bin/env bash
# =============================================================================
# StockHive Node Setup Script
# Installs this Pi as one StockPi instance (Kitchen, Tote Storage,
# Electronic Components, etc.) discoverable by a StockHive launcher on
# the same network.
#
# Run from the root of the cloned repo:
#   chmod +x setup-node.sh && sudo ./setup-node.sh
# =============================================================================

set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

if [[ $EUID -ne 0 ]]; then
  error "Please run with sudo: sudo ./setup-node.sh"
fi

REAL_USER="${SUDO_USER:-$(logname 2>/dev/null || echo pi)}"
REAL_HOME=$(getent passwd "$REAL_USER" | cut -d: -f6)

echo ""
echo -e "${BOLD}============================================${NC}"
echo -e "${BOLD}   StockHive Node Setup${NC}"
echo -e "${BOLD}============================================${NC}"
echo ""

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NODE_DIR="$REPO_DIR/node"
info "Repo directory : $REPO_DIR"
info "Installing for user: $REAL_USER (home: $REAL_HOME)"
echo ""

# =============================================================================
# 1. COLLECT CONFIGURATION
# =============================================================================
echo -e "${BOLD}--- Configuration ---${NC}"

read -rp "Instance name (default: Kitchen Inventory): " NODE_LABEL
NODE_LABEL="${NODE_LABEL:-Kitchen Inventory}"

echo "Theme: 1) dark  2) light  3) dim"
read -rp "Choose a theme [1-3, default 1]: " THEME_CHOICE
case "$THEME_CHOICE" in
  2) NODE_THEME="light" ;;
  3) NODE_THEME="dim" ;;
  *) NODE_THEME="dark" ;;
esac

echo ""

# =============================================================================
# 2. INSTALL SYSTEM PACKAGES
# =============================================================================
echo -e "${BOLD}--- Installing system packages ---${NC}"
apt-get update -qq
apt-get install -y python3 python3-pip python3-venv nginx > /dev/null 2>&1
success "System packages installed."

# =============================================================================
# 3. PYTHON VIRTUAL ENVIRONMENT + DEPENDENCIES
# =============================================================================
echo -e "${BOLD}--- Setting up Python virtual environment ---${NC}"
VENV_DIR="$NODE_DIR/venv"

if [[ -d "$VENV_DIR" ]]; then
  warn "venv already exists — skipping creation."
else
  sudo -u "$REAL_USER" python3 -m venv "$VENV_DIR"
  success "venv created."
fi

info "Installing Python dependencies..."
sudo -u "$REAL_USER" "$VENV_DIR/bin/pip" install --upgrade pip --quiet
sudo -u "$REAL_USER" "$VENV_DIR/bin/pip" install -r "$NODE_DIR/requirements.txt" --quiet
success "Python dependencies installed."

# =============================================================================
# 4. DATABASE + INSTANCE LABEL/THEME
# =============================================================================
echo -e "${BOLD}--- Initializing database ---${NC}"
cd "$NODE_DIR"
sudo -u "$REAL_USER" "$VENV_DIR/bin/python" db.py

sudo -u "$REAL_USER" "$VENV_DIR/bin/python" - "$NODE_LABEL" "$NODE_THEME" <<'PYEOF'
import sys
import labels
labels.set_node_label(sys.argv[1])
labels.set_theme(sys.argv[2])
PYEOF
success "Instance name set to \"$NODE_LABEL\" (theme: $NODE_THEME). Both are editable later from /settings."
cd "$REPO_DIR"

# =============================================================================
# 5. NGINX
# =============================================================================
echo -e "${BOLD}--- Configuring nginx ---${NC}"
cp "$REPO_DIR/nginx/node.conf" /etc/nginx/sites-available/stockpi-node.conf
ln -sf /etc/nginx/sites-available/stockpi-node.conf /etc/nginx/sites-enabled/stockpi-node.conf
rm -f /etc/nginx/sites-enabled/default
nginx -t > /dev/null 2>&1 && systemctl restart nginx
success "nginx configured and restarted."

# =============================================================================
# 6. SYSTEMD SERVICE
# =============================================================================
echo -e "${BOLD}--- Installing systemd service ---${NC}"
SERVICE_DEST="/etc/systemd/system/stockpi-node.service"
sed \
  -e "s|User=kinv|User=$REAL_USER|g" \
  -e "s|/home/kinv/node|$NODE_DIR|g" \
  "$REPO_DIR/systemd/stockpi-node.service" > "$SERVICE_DEST"

systemctl daemon-reload
systemctl enable stockpi-node.service
systemctl restart stockpi-node.service
success "stockpi-node.service installed and started."

# =============================================================================
# SUDOERS — allow the app to restart itself and git pull without a password
# =============================================================================
SUDOERS_FILE="/etc/sudoers.d/stockpi-node"
cat > "$SUDOERS_FILE" <<EOF
$REAL_USER ALL=(ALL) NOPASSWD: /bin/systemctl restart stockpi-node.service
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
echo -e "  This node should appear on your StockHive launcher within a few seconds."
echo -e "  Rename it or change its theme any time from: ${CYAN}http://${LOCAL_IP}/settings${NC}"
echo ""
