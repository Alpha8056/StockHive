#!/usr/bin/env bash
# =============================================================================
# StockHive Setup Script
# Installs a node (Kitchen Inventory, Tote Storage, etc.), the launcher
# (mDNS discovery + weather + node grid), or both on this machine.
#
# Run from the root of the cloned repo:
#   chmod +x setup.sh && sudo ./setup.sh
# =============================================================================

set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

if [[ $EUID -ne 0 ]]; then
  error "Please run with sudo: sudo ./setup.sh"
fi

REAL_USER="${SUDO_USER:-$(logname 2>/dev/null || echo pi)}"
REAL_HOME=$(getent passwd "$REAL_USER" | cut -d: -f6)
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo -e "${BOLD}============================================${NC}"
echo -e "${BOLD}   StockHive Setup${NC}"
echo -e "${BOLD}============================================${NC}"
echo ""
info "Repo directory : $REPO_DIR"
info "Installing for user: $REAL_USER (home: $REAL_HOME)"
echo ""

# =============================================================================
# 1. WHAT TO INSTALL
# =============================================================================
echo -e "${BOLD}--- What do you want to install on this machine? ---${NC}"
echo "  1) A node (Kitchen Inventory, Tote Storage, etc.)"
echo "  2) The launcher (discovery grid + weather) — recommended on its own device"
echo "  3) Both, on this same machine"
read -rp "Choose [1-3]: " INSTALL_CHOICE

INSTALL_NODE=false
INSTALL_LAUNCHER=false
case "$INSTALL_CHOICE" in
  1) INSTALL_NODE=true ;;
  2) INSTALL_LAUNCHER=true ;;
  3) INSTALL_NODE=true; INSTALL_LAUNCHER=true ;;
  *) error "Please choose 1, 2, or 3." ;;
esac

NODE_PORT=80
LAUNCHER_PORT=80

if $INSTALL_NODE && $INSTALL_LAUNCHER; then
  warn "Installing both on one machine — nginx can't have both listen on the"
  warn "same port, so they need different ports. Pick your own or use the defaults."
  read -rp "Node port [default 80]: " NODE_PORT_IN
  NODE_PORT="${NODE_PORT_IN:-80}"
  while true; do
    read -rp "Launcher port [default 8080]: " LAUNCHER_PORT_IN
    LAUNCHER_PORT="${LAUNCHER_PORT_IN:-8080}"
    if [[ "$LAUNCHER_PORT" != "$NODE_PORT" ]]; then
      break
    fi
    warn "Launcher port must be different from the node port ($NODE_PORT)."
  done
elif $INSTALL_NODE; then
  read -rp "Node port [default 80]: " NODE_PORT_IN
  NODE_PORT="${NODE_PORT_IN:-80}"
elif $INSTALL_LAUNCHER; then
  read -rp "Launcher port [default 80]: " LAUNCHER_PORT_IN
  LAUNCHER_PORT="${LAUNCHER_PORT_IN:-80}"
fi

if $INSTALL_NODE; then
  echo ""
  echo -e "${BOLD}--- Node configuration ---${NC}"
  read -rp "Instance name (default: Kitchen Inventory): " NODE_LABEL
  NODE_LABEL="${NODE_LABEL:-Kitchen Inventory}"
  echo "Theme: 1) dark  2) light  3) dim"
  read -rp "Choose a theme [1-3, default 1]: " THEME_CHOICE
  case "$THEME_CHOICE" in
    2) NODE_THEME="light" ;;
    3) NODE_THEME="dim" ;;
    *) NODE_THEME="dark" ;;
  esac
fi

if $INSTALL_LAUNCHER; then
  echo ""
  echo -e "${BOLD}--- Launcher configuration ---${NC}"
  while true; do
    read -rp "ZIP code for the weather widget (5 digits): " ZIP_CODE
    [[ "$ZIP_CODE" =~ ^[0-9]{5}$ ]] && break
    warn "Please enter a valid 5-digit ZIP code."
  done
fi
echo ""

# =============================================================================
# 2. SYSTEM PACKAGES (shared)
# =============================================================================
echo -e "${BOLD}--- Installing system packages ---${NC}"
apt-get update -qq
apt-get install -y python3 python3-pip python3-venv nginx > /dev/null 2>&1
success "System packages installed."

# =============================================================================
# NODE INSTALL
# =============================================================================
install_node() {
  local NODE_DIR="$REPO_DIR/node"
  echo ""
  echo -e "${BOLD}--- Setting up node (port $NODE_PORT) ---${NC}"

  local VENV_DIR="$NODE_DIR/venv"
  if [[ -d "$VENV_DIR" ]]; then
    warn "Node venv already exists — skipping creation."
  else
    sudo -u "$REAL_USER" python3 -m venv "$VENV_DIR"
  fi
  sudo -u "$REAL_USER" "$VENV_DIR/bin/pip" install --upgrade pip --quiet
  sudo -u "$REAL_USER" "$VENV_DIR/bin/pip" install -r "$NODE_DIR/requirements.txt" --quiet
  success "Node dependencies installed."

  (
    cd "$NODE_DIR"
    sudo -u "$REAL_USER" "$VENV_DIR/bin/python" db.py
    sudo -u "$REAL_USER" "$VENV_DIR/bin/python" - "$NODE_LABEL" "$NODE_THEME" <<'PYEOF'
import sys
import labels
labels.set_node_label(sys.argv[1])
labels.set_theme(sys.argv[2])
PYEOF
  )
  success "Instance name set to \"$NODE_LABEL\" (theme: $NODE_THEME). Editable later from /settings."

  sed "s|__LISTEN_PORT__|$NODE_PORT|g" "$REPO_DIR/nginx/node.conf" > /etc/nginx/sites-available/stockpi-node.conf
  ln -sf /etc/nginx/sites-available/stockpi-node.conf /etc/nginx/sites-enabled/stockpi-node.conf

  sed \
    -e "s|User=kinv|User=$REAL_USER|g" \
    -e "s|/home/kinv/node|$NODE_DIR|g" \
    -e "s|__PUBLIC_PORT__|$NODE_PORT|g" \
    "$REPO_DIR/systemd/stockpi-node.service" > /etc/systemd/system/stockpi-node.service

  systemctl daemon-reload
  systemctl enable stockpi-node.service
  systemctl restart stockpi-node.service

  cat > /etc/sudoers.d/stockpi-node <<EOF
$REAL_USER ALL=(ALL) NOPASSWD: /bin/systemctl restart stockpi-node.service
EOF
  chmod 0440 /etc/sudoers.d/stockpi-node
  success "stockpi-node.service installed and started on port $NODE_PORT."
}

# =============================================================================
# LAUNCHER INSTALL
# =============================================================================
install_launcher() {
  local LAUNCHER_DIR="$REPO_DIR/launcher"
  echo ""
  echo -e "${BOLD}--- Setting up launcher (port $LAUNCHER_PORT) ---${NC}"

  local CONFIG_PATH="$LAUNCHER_DIR/config.json"
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
  fi
  mkdir -p "$LAUNCHER_DIR/data_cache"
  chown -R "$REAL_USER":"$REAL_USER" "$LAUNCHER_DIR/data_cache"

  local VENV_DIR="$LAUNCHER_DIR/venv"
  if [[ -d "$VENV_DIR" ]]; then
    warn "Launcher venv already exists — skipping creation."
  else
    sudo -u "$REAL_USER" python3 -m venv "$VENV_DIR"
  fi
  sudo -u "$REAL_USER" "$VENV_DIR/bin/pip" install --upgrade pip --quiet
  sudo -u "$REAL_USER" "$VENV_DIR/bin/pip" install -r "$LAUNCHER_DIR/requirements.txt" --quiet
  success "Launcher dependencies installed."

  sed "s|__LISTEN_PORT__|$LAUNCHER_PORT|g" "$REPO_DIR/nginx/launcher.conf" > /etc/nginx/sites-available/stockpi-launcher.conf
  ln -sf /etc/nginx/sites-available/stockpi-launcher.conf /etc/nginx/sites-enabled/stockpi-launcher.conf

  sed \
    -e "s|User=kinv|User=$REAL_USER|g" \
    -e "s|/home/kinv/launcher|$LAUNCHER_DIR|g" \
    -e "s|__PUBLIC_PORT__|$LAUNCHER_PORT|g" \
    "$REPO_DIR/systemd/stockpi-launcher.service" > /etc/systemd/system/stockpi-launcher.service

  systemctl daemon-reload
  systemctl enable stockpi-launcher.service
  systemctl restart stockpi-launcher.service

  cat > /etc/sudoers.d/stockpi-launcher <<EOF
$REAL_USER ALL=(ALL) NOPASSWD: /bin/systemctl restart stockpi-launcher.service
EOF
  chmod 0440 /etc/sudoers.d/stockpi-launcher
  success "stockpi-launcher.service installed and started on port $LAUNCHER_PORT."
}

# This script assumes it owns nginx on this machine (a dedicated
# node/launcher Pi) — clear out any other enabled sites first. Without
# this, a leftover site from an older install (e.g. a Pi that used to run
# v1) can conflict with our own (both declaring `default_server` on the
# same port), nginx's config test fails, and — critically — everything
# below used to hide that failure and just leave nginx silently serving
# whatever was already loaded, with no indication anything was wrong.
rm -f /etc/nginx/sites-enabled/*

$INSTALL_NODE && install_node
$INSTALL_LAUNCHER && install_launcher

if ! nginx -t; then
  error "nginx config test failed (see above) — nginx was NOT reloaded, so it may still be serving old content. Fix the error and run: sudo systemctl restart nginx"
fi
systemctl restart nginx
success "nginx configured and restarted."

# =============================================================================
# DONE
# =============================================================================
echo ""
echo -e "${BOLD}============================================${NC}"
echo -e "${GREEN}${BOLD}   Setup complete!${NC}"
echo -e "${BOLD}============================================${NC}"
echo ""

LOCAL_IP=$(hostname -I | awk '{print $1}')
if $INSTALL_NODE; then
  suffix=""; [[ "$NODE_PORT" != "80" ]] && suffix=":$NODE_PORT"
  echo -e "  Node:     ${CYAN}http://${LOCAL_IP}${suffix}${NC}  (rename/theme any time from /settings)"
fi
if $INSTALL_LAUNCHER; then
  suffix=""; [[ "$LAUNCHER_PORT" != "80" ]] && suffix=":$LAUNCHER_PORT"
  echo -e "  Launcher: ${CYAN}http://${LOCAL_IP}${suffix}${NC}"
fi
echo ""
