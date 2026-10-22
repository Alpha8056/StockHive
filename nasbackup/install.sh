#!/usr/bin/env bash
# =============================================================================
# StockHive nightly backup — installer for the backup machine (NAS / Linux box)
#
#   sudo ./install.sh
#
# First run: creates /etc/stockhive-backup.conf from the example and stops so
# you can edit it. Run again to install/refresh the systemd service + timer
# (also after changing RUN_AT).
# =============================================================================
set -euo pipefail

CONFIG=/etc/stockhive-backup.conf
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$HERE/stockhive_backup.py"

[[ $EUID -eq 0 ]] || { echo "Please run with sudo: sudo ./install.sh"; exit 1; }
RUN_USER="${SUDO_USER:-root}"
command -v python3 >/dev/null || { echo "python3 is required"; exit 1; }

if [[ ! -f "$CONFIG" ]]; then
  cp "$HERE/stockhive-backup.conf.example" "$CONFIG"
  chmod 644 "$CONFIG"
  echo "Created $CONFIG — edit LAUNCHER_URL, BACKUP_DIR, KEEP_DAYS and RUN_AT,"
  echo "then run: sudo ./install.sh"
  exit 0
fi

# Read values without sourcing the file as shell.
conf() { grep -E "^[[:space:]]*$1=" "$CONFIG" | tail -n1 | cut -d= -f2- | tr -d "\"'" | xargs; }
RUN_AT="$(conf RUN_AT)"
BACKUP_DIR="$(conf BACKUP_DIR)"
RUN_AT="${RUN_AT:-02:30}"

[[ "$RUN_AT" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]] || { echo "RUN_AT must be HH:MM (24-hour), got: $RUN_AT"; exit 1; }
[[ -n "$BACKUP_DIR" ]] || { echo "BACKUP_DIR is empty in $CONFIG"; exit 1; }

mkdir -p "$BACKUP_DIR"
chown "$RUN_USER": "$BACKUP_DIR"

sed -e "s|__USER__|$RUN_USER|" -e "s|__SCRIPT__|$SCRIPT|" -e "s|__CONFIG__|$CONFIG|" \
  "$HERE/stockhive-backup.service" > /etc/systemd/system/stockhive-backup.service
sed -e "s|__RUN_AT__|$RUN_AT|" \
  "$HERE/stockhive-backup.timer" > /etc/systemd/system/stockhive-backup.timer

systemctl daemon-reload
systemctl enable --now stockhive-backup.timer >/dev/null
echo "Installed. Backups run daily at $RUN_AT as $RUN_USER into $BACKUP_DIR."
systemctl list-timers stockhive-backup.timer --no-pager
