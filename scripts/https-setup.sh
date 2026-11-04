#!/usr/bin/env bash
# =============================================================================
# StockHive HTTPS helper: a local certificate authority (CA) for nodes.
#
# Phones only allow the live camera scanner on https:// pages. This script
# creates one private CA (trusted once on each phone) and uses it to issue
# a certificate to each node. HTTP keeps working exactly as before.
#
#   On your CA machine (a computer that is NOT a node):
#     scripts/https-setup.sh ca-create
#     scripts/https-setup.sh issue root@<node-ip>     # or user@<node-ip>
#     scripts/https-setup.sh renew root@<node-ip>     # near expiry / new IP
#
#   On a node (sudo):
#     sudo scripts/https-setup.sh status
#     sudo scripts/https-setup.sh node-remove
#
#   Without SSH (copy the files yourself):
#     node:       sudo scripts/https-setup.sh node-request > node.csr
#     CA machine: scripts/https-setup.sh sign node.csr     (writes node.crt)
#     node:       sudo scripts/https-setup.sh node-install node.crt ca.crt
#
# Safety:
#   - The CA private key is created on the CA machine, encrypted with a
#     passphrase, and never sent anywhere. This script refuses to create
#     it on a node or inside a git checkout.
#   - Each node's private key is created on that node and never leaves it.
#   - Nodes only receive their own certificate and the PUBLIC CA cert.
#   - The CA can only sign .local names and private LAN addresses
#     (10.x, 172.16-31.x, 192.168.x), so even a stolen CA key can't be
#     used to fake real websites on phones that trust it.
#
# Full walkthrough: docs/HTTPS.md
# =============================================================================

set -euo pipefail

TLS_DIR="${STOCKHIVE_TLS_DIR:-/etc/stockhive/tls}"
NGINX_AVAIL=/etc/nginx/sites-available/stockpi-node-https.conf
NGINX_ENABLED=/etc/nginx/sites-enabled/stockpi-node-https.conf
NODE_UNIT=/etc/systemd/system/stockpi-node.service
CA_DIR_DEFAULT="${HOME:-~}/stockhive-ca"
CA_DAYS=3650
CERT_DAYS=825          # iPhones reject server certificates valid for longer
DEFAULT_HTTPS_PORT=443

# Where the nginx template lives when run from a repo checkout. When this
# script is streamed to a node over SSH there is no checkout; `issue`
# sends the template along instead.
SELF="${BASH_SOURCE[0]:-}"
SCRIPT_DIR=""
[[ -n "$SELF" && -f "$SELF" ]] && SCRIPT_DIR="$(cd "$(dirname "$SELF")" && pwd)"
TEMPLATE="${SCRIPT_DIR:+$SCRIPT_DIR/../nginx/node-https.conf}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
# All messages go to stderr: `node-request` prints the CSR on stdout.
info()    { echo -e "${CYAN}[INFO]${NC}  $*" >&2; }
success() { echo -e "${GREEN}[OK]${NC}    $*" >&2; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*" >&2; }
die()     { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

usage() {
  sed -n '2,/^# =====/p' "$SELF" 2>/dev/null | sed 's/^# \{0,1\}//' | sed '$d' >&2 || true
  exit "${1:-0}"
}

need_openssl() { command -v openssl >/dev/null 2>&1 || die "openssl is not installed (sudo apt install openssl)."; }
require_root() { [[ "$(id -u)" -eq 0 ]] || die "Run this with sudo: sudo $0 $*"; }

TMP=""
cleanup() { [[ -n "$TMP" && -d "$TMP" ]] && rm -rf "$TMP"; return 0; }
trap cleanup EXIT
mktmp() { [[ -n "$TMP" ]] || TMP="$(mktemp -d)"; }

b64enc() { base64 < "$1" | tr -d '\n'; }

# ---------------------------------------------------------------------------
# Name checks (must match the CA's name constraints)
# ---------------------------------------------------------------------------
is_private_ip() {
  local ip="$1" a b c d
  [[ "$ip" =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]] || return 1
  a=${BASH_REMATCH[1]}; b=${BASH_REMATCH[2]}; c=${BASH_REMATCH[3]}; d=${BASH_REMATCH[4]}
  (( a <= 255 && b <= 255 && c <= 255 && d <= 255 )) || return 1
  (( a == 10 )) && return 0
  (( a == 172 && b >= 16 && b <= 31 )) && return 0
  (( a == 192 && b == 168 )) && return 0
  return 1
}
is_local_name() { [[ "$1" =~ ^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+local$ ]]; }

# "DNS:a.local IP:192.168.1.20" -> checked; dies on anything the CA can't sign.
check_sans() {
  local san
  for san in "$@"; do
    case "$san" in
      DNS:*) is_local_name "${san#DNS:}" || die "Name '${san#DNS:}' isn't allowed: only names ending in .local." ;;
      IP:*)  is_private_ip "${san#IP:}" || die "Address '${san#IP:}' isn't allowed: only private LAN addresses (10.x, 172.16-31.x, 192.168.x)." ;;
      *)     die "Unknown name '$san'." ;;
    esac
  done
}

# ---------------------------------------------------------------------------
# CA machine: ca-create
# ---------------------------------------------------------------------------
cmd_ca_create() {
  local dir="$CA_DIR_DEFAULT"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dir) dir="${2:?--dir needs a folder}"; shift 2 ;;
      *) die "Unknown option: $1" ;;
    esac
  done
  need_openssl
  [[ -f "$NODE_UNIT" ]] && die "This machine runs a StockHive node. Create the CA on a computer that isn't a node (see docs/HTTPS.md)."
  [[ -e "$dir/ca.key" ]] && die "A CA already exists in $dir. Use it, or pick another folder with --dir."

  mkdir -p "$dir"
  if git -C "$dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    rmdir "$dir" 2>/dev/null || true
    die "$dir is inside a git checkout. Keep the CA outside any repo (the default is ~/stockhive-ca)."
  fi
  chmod 700 "$dir"

  mktmp
  cat > "$TMP/ca.cnf" <<'EOF'
[req]
distinguished_name = dn
prompt = no
x509_extensions = v3_ca
[dn]
CN = StockHive Local CA
O = StockHive (private, this home network only)
[v3_ca]
basicConstraints = critical, CA:TRUE, pathlen:0
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
nameConstraints = critical, permitted;DNS:local, permitted;IP:10.0.0.0/255.0.0.0, permitted;IP:172.16.0.0/255.240.0.0, permitted;IP:192.168.0.0/255.255.0.0
EOF

  info "Creating the CA key. Choose a passphrase and keep it in your password manager:"
  info "you'll need it every time you issue or renew a node certificate."
  ( umask 077; openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -aes-256-cbc -out "$dir/ca.key" ) \
    || { rm -f "$dir/ca.key"; die "Couldn't create the CA key."; }
  openssl req -x509 -new -config "$TMP/ca.cnf" -key "$dir/ca.key" -sha256 -days "$CA_DAYS" \
      -set_serial "0x$(openssl rand -hex 16)" -out "$dir/ca.crt" \
    || { rm -f "$dir/ca.key" "$dir/ca.crt"; die "Couldn't create the CA certificate."; }
  chmod 600 "$dir/ca.key"; chmod 644 "$dir/ca.crt"

  success "CA created in $dir"
  echo "" >&2
  echo "  ca.key  PRIVATE, encrypted. Never copy it to a node or commit it." >&2
  echo "  ca.crt  public. This is what phones trust." >&2
  echo "" >&2
  echo "  Fingerprint (compare on the phone / node Settings):" >&2
  echo "  $(openssl x509 -in "$dir/ca.crt" -noout -fingerprint -sha256 | sed 's/^.*=//')" >&2
  echo "" >&2
  echo "  Back up this whole folder (e.g. to a USB stick). Keep the passphrase separate." >&2
  echo "  Next: $0 issue root@<node-ip>" >&2
}

# ---------------------------------------------------------------------------
# CA machine: sign a CSR
# ---------------------------------------------------------------------------
csr_sans() {
  # Prints the CSR's subjectAltName entries as "DNS:x IP:y ..."
  openssl req -in "$1" -noout -text \
    | sed -n -e '/Subject Alternative Name/{' -e 'n' -e 'p' -e '}' \
    | sed -e 's/IP Address:/IP:/g' -e 's/,/ /g' | xargs
}

sign_csr() {   # sign_csr <csr> <out.crt> <ca-dir>
  local csr="$1" out="$2" dir="$3" sans
  need_openssl
  [[ -f "$dir/ca.key" && -f "$dir/ca.crt" ]] || die "No CA in $dir. Run: $0 ca-create (or pass --dir)."
  openssl req -in "$csr" -noout -verify >/dev/null 2>&1 || die "$csr isn't a valid certificate request."

  sans="$(csr_sans "$csr")"
  [[ -n "$sans" ]] || die "The request has no names or addresses in it."
  # shellcheck disable=SC2086
  check_sans $sans

  echo "" >&2
  info "The node asks for a certificate covering:"
  local s; for s in $sans; do echo "          $s" >&2; done
  echo "" >&2
  local answer=""
  read -r -p "Sign it? [y/N] " answer < /dev/tty || true
  [[ "$answer" =~ ^[Yy] ]] || die "Not signed."

  mktmp
  {
    echo "basicConstraints = critical, CA:FALSE"
    echo "keyUsage = critical, digitalSignature, keyEncipherment"
    echo "extendedKeyUsage = serverAuth"
    echo "subjectKeyIdentifier = hash"
    echo "authorityKeyIdentifier = keyid"
    echo "subjectAltName = $(echo "$sans" | sed 's/ /, /g')"
  } > "$TMP/leaf.ext"

  info "Enter the CA passphrase:"
  openssl x509 -req -in "$csr" -CA "$dir/ca.crt" -CAkey "$dir/ca.key" \
      -set_serial "0x$(openssl rand -hex 16)" -days "$CERT_DAYS" -sha256 \
      -extfile "$TMP/leaf.ext" -out "$out" \
    || die "Signing failed (wrong passphrase?)."
  openssl verify -CAfile "$dir/ca.crt" "$out" >/dev/null || die "The new certificate didn't verify against the CA."
  success "Signed: valid until $(openssl x509 -in "$out" -noout -enddate | sed 's/^notAfter=//')"
}

cmd_sign() {
  local csr="" out="" dir="$CA_DIR_DEFAULT"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dir) dir="${2:?}"; shift 2 ;;
      --out) out="${2:?}"; shift 2 ;;
      -*) die "Unknown option: $1" ;;
      *) csr="$1"; shift ;;
    esac
  done
  [[ -n "$csr" && -f "$csr" ]] || die "Usage: $0 sign <node.csr> [--out node.crt] [--dir CA-folder]"
  out="${out:-${csr%.csr}.crt}"
  sign_csr "$csr" "$out" "$dir"
  echo "" >&2
  echo "  Copy these two files to the node, then run there:" >&2
  echo "    sudo scripts/https-setup.sh node-install $(basename "$out") ca.crt" >&2
  echo "  ($out and $dir/ca.crt. Never copy ca.key.)" >&2
}

# ---------------------------------------------------------------------------
# CA machine: issue / renew over SSH
# ---------------------------------------------------------------------------
cmd_issue() {
  local target="" dir="$CA_DIR_DEFAULT" port="" new_key=false
  local -a extra=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dir)     dir="${2:?}"; shift 2 ;;
      --ip)      is_private_ip "${2:-}" || die "--ip needs a private LAN address."; extra+=("--ip" "$2"); shift 2 ;;
      --name)    is_local_name "${2:-}" || die "--name needs a name ending in .local."; extra+=("--name" "$2"); shift 2 ;;
      --port)    [[ "${2:-}" =~ ^[0-9]+$ ]] || die "--port needs a number."; port="$2"; shift 2 ;;
      --new-key) new_key=true; shift ;;
      --reset-names) extra+=("--reset-names"); shift ;;
      -*) die "Unknown option: $1" ;;
      *) target="$1"; shift ;;
    esac
  done
  [[ -n "$target" ]] || die "Usage: $0 issue <user@node-ip> [--ip IP] [--name NAME.local] [--port 443] [--new-key]"
  [[ "$target" =~ ^[A-Za-z0-9._@:-]+$ ]] || die "That doesn't look like user@host."
  [[ -f "$dir/ca.key" ]] || die "No CA in $dir. Run: $0 ca-create (or pass --dir)."
  [[ -n "$TEMPLATE" && -f "$TEMPLATE" ]] || die "Run this from a StockHive checkout (needs nginx/node-https.conf)."
  command -v ssh >/dev/null 2>&1 || die "ssh is not installed."
  $new_key && extra+=("--new-key")

  # Runs this script on the node as root (directly, or with passwordless
  # sudo). The script itself goes over stdin, so the node needs no copy.
  # Every argument here is checked above or is base64, so it's shell-safe.
  remote() {
    ssh "$target" "if [ \"\$(id -u)\" -eq 0 ]; then bash -s -- $*; else sudo -n bash -s -- $*; fi" < "$SELF"
  }

  mktmp
  info "Asking $target for a certificate request..."
  remote node-request ${extra[@]+"${extra[@]}"} > "$TMP/node.csr" \
    || die "The node couldn't make a request. SSH in as root, or a user with passwordless sudo (or use the manual steps in docs/HTTPS.md)."
  grep -q "BEGIN CERTIFICATE REQUEST" "$TMP/node.csr" || die "The node didn't send a certificate request."

  sign_csr "$TMP/node.csr" "$TMP/node.crt" "$dir"

  info "Installing on $target..."
  local -a inst=(node-install-b64 "$(b64enc "$TMP/node.crt")" "$(b64enc "$dir/ca.crt")" "$(b64enc "$TEMPLATE")")
  [[ -n "$port" ]] && inst+=(--port "$port")
  remote "${inst[@]}" || die "Install on the node failed (see the messages above). HTTP is unaffected."
  success "Done."
}

# ---------------------------------------------------------------------------
# Node: settings file (/etc/stockhive/tls/https.env)
# ---------------------------------------------------------------------------
ENV_FILE="$TLS_DIR/https.env"
env_get() {   # env_get KEY  (values are only digits, 0/1, or SAN tokens)
  [[ -f "$ENV_FILE" ]] || return 0
  sed -n "s/^$1=\"\{0,1\}\([^\"]*\)\"\{0,1\}\$/\1/p" "$ENV_FILE" | tail -n 1
}
env_write() { # env_write ENABLED PORT EXTRA_SANS
  mkdir -p "$TLS_DIR"
  {
    echo "# Written by scripts/https-setup.sh. Read by the node app and setup.sh."
    echo "HTTPS_ENABLED=$1"
    echo "HTTPS_PORT=$2"
    echo "EXTRA_SANS=\"$3\""
  } > "$ENV_FILE.tmp"
  chmod 644 "$ENV_FILE.tmp"; mv "$ENV_FILE.tmp" "$ENV_FILE"
}

lan_ip() {
  # Same choice as the node app: the address with the default route.
  local ip=""
  ip="$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -n 1)" || true
  [[ -n "$ip" ]] || ip="$(hostname -I 2>/dev/null | awk '{print $1}')" || true
  echo "$ip"
}

# ---------------------------------------------------------------------------
# Node: node-request (prints a CSR on stdout)
# ---------------------------------------------------------------------------
cmd_node_request() {
  local new_key=false reset=false
  local -a extra=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --ip)   extra+=("IP:${2:?}"); shift 2 ;;
      --name) extra+=("DNS:${2:?}"); shift 2 ;;
      --new-key) new_key=true; shift ;;
      --reset-names) reset=true; shift ;;
      *) die "Unknown option: $1" ;;
    esac
  done
  require_root node-request
  need_openssl
  [[ -f "$NODE_UNIT" ]] || die "There's no StockHive node on this machine."

  local host ip
  host="$(hostname -s 2>/dev/null || hostname)"; host="$(echo "$host" | tr 'A-Z' 'a-z')"
  ip="$(lan_ip)"
  local -a sans=()
  is_local_name "$host.local" && sans+=("DNS:$host.local") || warn "Hostname '$host' can't be used as a .local name; using the IP only."
  if is_private_ip "$ip"; then sans+=("IP:$ip"); else warn "Couldn't find a private LAN address (got '${ip:-none}'). Pass one with --ip."; fi

  # Extra names are remembered, so a renew keeps them.
  local stored=""; $reset || stored="$(env_get EXTRA_SANS)"
  local s
  for s in $stored ${extra[@]+"${extra[@]}"}; do
    [[ " ${sans[*]} " == *" $s "* ]] || sans+=("$s")
  done
  [[ ${#sans[@]} -gt 0 ]] || die "Nothing to put in the certificate."
  check_sans "${sans[@]}"

  # Keep the extras (everything except the auto-detected host/IP).
  local keep=""
  for s in "${sans[@]}"; do
    [[ "$s" == "DNS:$host.local" || "$s" == "IP:$ip" ]] || keep="${keep:+$keep }$s"
  done

  mkdir -p "$TLS_DIR"; chmod 755 "$TLS_DIR"
  local key="$TLS_DIR/node.key"
  if $new_key || [[ ! -f "$key" ]]; then
    [[ -f "$key" ]] && key="$TLS_DIR/node.key.pending"   # swapped in by node-install
    ( umask 077; openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "$key" 2>/dev/null ) \
      || die "Couldn't create the node key."
    chmod 600 "$key"
  fi

  mktmp
  {
    echo "[req]"; echo "distinguished_name = dn"; echo "prompt = no"; echo "req_extensions = ext"
    echo "[dn]";  echo "CN = ${sans[0]#*:}"
    echo "[ext]"; echo "subjectAltName = $(IFS=,; echo "${sans[*]}" | sed 's/,/, /g')"
  } > "$TMP/req.cnf"
  openssl req -new -config "$TMP/req.cnf" -key "$key" -sha256 -out "$TLS_DIR/node.csr" 2>/dev/null \
    || die "Couldn't create the certificate request."
  chmod 644 "$TLS_DIR/node.csr"

  # Remember extras now; enabled/port are set by node-install.
  env_write "$(env_get HTTPS_ENABLED || true)" "$(env_get HTTPS_PORT || true)" "$keep"

  if ! systemctl is-active --quiet avahi-daemon 2>/dev/null; then
    warn "avahi-daemon isn't running, so $host.local may not work from phones. The IP address always works."
  fi
  info "Request covers: ${sans[*]}"
  cat "$TLS_DIR/node.csr"
}

# ---------------------------------------------------------------------------
# Node: node-install
# ---------------------------------------------------------------------------
pubkey_of_cert() { openssl x509 -in "$1" -noout -pubkey 2>/dev/null; }
pubkey_of_key()  { openssl pkey -in "$1" -pubout 2>/dev/null; }

listen_ports_in() { [[ -f "$1" ]] && sed -n 's/^[[:space:]]*listen[[:space:]]\{1,\}\([0-9]\{1,\}\).*/\1/p' "$1" | sort -u; return 0; }

install_files() {   # install_files <node.crt> <ca.crt> <template> <port-or-empty>
  local crt="$1" ca="$2" tmpl="$3" port="$4"
  require_root node-install
  need_openssl
  [[ -f "$NODE_UNIT" ]] || die "There's no StockHive node on this machine."
  command -v nginx >/dev/null 2>&1 || die "nginx isn't installed. Run setup.sh first."

  # --- check what we were given ---
  local f
  for f in "$crt" "$ca" "$tmpl"; do [[ -f "$f" ]] || die "Missing file: $f"; done
  if grep -q "PRIVATE KEY" "$crt" "$ca"; then die "A private key was passed in. Only certificates belong on a node."; fi
  openssl x509 -in "$crt" -noout 2>/dev/null || die "$crt isn't a certificate."
  openssl x509 -in "$ca"  -noout 2>/dev/null || die "$ca isn't a certificate."
  openssl x509 -in "$ca" -noout -text | grep -q "CA:TRUE" || die "$ca isn't a CA certificate."
  openssl verify -CAfile "$ca" "$crt" >/dev/null 2>&1 || die "The certificate wasn't signed by that CA (or breaks its name limits)."
  grep -q "__HTTPS_PORT__" "$tmpl" || die "The nginx template looks wrong."

  local use_pending=false
  if [[ -f "$TLS_DIR/node.key.pending" ]] && [[ "$(pubkey_of_cert "$crt")" == "$(pubkey_of_key "$TLS_DIR/node.key.pending")" ]]; then
    use_pending=true
  elif [[ ! -f "$TLS_DIR/node.key" ]] || [[ "$(pubkey_of_cert "$crt")" != "$(pubkey_of_key "$TLS_DIR/node.key")" ]]; then
    die "This certificate doesn't match this node's key. Run issue/renew again."
  fi

  # --- port ---
  port="${port:-$(env_get HTTPS_PORT)}"; port="${port:-$DEFAULT_HTTPS_PORT}"
  [[ "$port" =~ ^[0-9]+$ ]] && (( port > 0 && port < 65536 )) || die "Bad port: $port"
  local p
  for p in $(listen_ports_in /etc/nginx/sites-available/stockpi-node.conf) $(listen_ports_in /etc/nginx/sites-available/stockpi-launcher.conf); do
    [[ "$p" == "$port" ]] && die "Port $port is already used by StockHive's HTTP site. Pick another with --port (e.g. 8443)."
  done
  if command -v ss >/dev/null 2>&1; then
    if ss -Hltnp "sport = :$port" 2>/dev/null | grep -v nginx | grep -q .; then
      die "Something other than nginx is already using port $port. Pick another with --port (e.g. 8443)."
    fi
  fi

  # --- install, keeping the old files for rollback ---
  mkdir -p "$TLS_DIR"; chmod 755 "$TLS_DIR"
  for f in node.crt ca.crt node.key; do
    [[ -f "$TLS_DIR/$f" ]] && cp -p "$TLS_DIR/$f" "$TLS_DIR/$f.prev"
  done
  local had_site=false; [[ -f "$NGINX_AVAIL" ]] && { had_site=true; cp -p "$NGINX_AVAIL" "$TLS_DIR/nginx.prev"; }
  local old_env=""; [[ -f "$ENV_FILE" ]] && old_env="$(cat "$ENV_FILE")"

  rollback() {
    warn "Rolling back."
    for f in node.crt ca.crt node.key; do
      [[ -f "$TLS_DIR/$f.prev" ]] && mv -f "$TLS_DIR/$f.prev" "$TLS_DIR/$f"
    done
    if $had_site; then mv -f "$TLS_DIR/nginx.prev" "$NGINX_AVAIL"; else rm -f "$NGINX_AVAIL" "$NGINX_ENABLED"; fi
    if [[ -n "$old_env" ]]; then printf '%s\n' "$old_env" > "$ENV_FILE"; fi
    nginx -t >/dev/null 2>&1 && systemctl reload nginx || true
  }

  install -m 644 "$crt" "$TLS_DIR/node.crt"
  install -m 644 "$ca"  "$TLS_DIR/ca.crt"
  if $use_pending; then mv -f "$TLS_DIR/node.key.pending" "$TLS_DIR/node.key"; fi
  chown root:root "$TLS_DIR/node.key"; chmod 600 "$TLS_DIR/node.key"

  sed "s|__HTTPS_PORT__|$port|g" "$tmpl" > "$NGINX_AVAIL"
  ln -sf "$NGINX_AVAIL" "$NGINX_ENABLED"
  if ! nginx -t 2>/dev/null; then
    nginx -t || true
    rollback
    die "nginx rejected the HTTPS config, so nothing was changed. HTTP still works."
  fi
  if ! systemctl reload nginx; then
    rollback
    die "nginx couldn't reload. Nothing was changed. HTTP still works."
  fi
  env_write 1 "$port" "$(env_get EXTRA_SANS)"
  rm -f "$TLS_DIR"/*.prev "$TLS_DIR/nginx.prev"

  # Restart the node so it advertises HTTPS to the launcher and shows it in Settings.
  systemctl restart stockpi-node.service || warn "Couldn't restart stockpi-node; restart it to update Settings."

  local suffix="" addr; [[ "$port" != "443" ]] && suffix=":$port"
  addr="$(lan_ip)"; addr="${addr:-<node-ip>}"
  success "HTTPS is on: https://$addr$suffix/"
  print_status
}

cmd_node_install() {
  local crt="" ca="" port=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --port) port="${2:?}"; shift 2 ;;
      -*) die "Unknown option: $1" ;;
      *) if [[ -z "$crt" ]]; then crt="$1"; elif [[ -z "$ca" ]]; then ca="$1"; else die "Too many files."; fi; shift ;;
    esac
  done
  [[ -n "$crt" && -n "$ca" ]] || die "Usage: sudo $0 node-install <node.crt> <ca.crt> [--port 443]"
  [[ -n "$TEMPLATE" && -f "$TEMPLATE" ]] || die "Run this from the StockHive checkout on the node (needs nginx/node-https.conf)."
  install_files "$crt" "$ca" "$TEMPLATE" "$port"
}

cmd_node_install_b64() {   # used by `issue` over SSH
  local crt_b64="${1:?}" ca_b64="${2:?}" tmpl_b64="${3:?}"; shift 3
  local port=""
  [[ "${1:-}" == "--port" ]] && port="${2:?}"
  mktmp
  printf '%s' "$crt_b64"  | base64 -d > "$TMP/node.crt"
  printf '%s' "$ca_b64"   | base64 -d > "$TMP/ca.crt"
  printf '%s' "$tmpl_b64" | base64 -d > "$TMP/node-https.conf"
  install_files "$TMP/node.crt" "$TMP/ca.crt" "$TMP/node-https.conf" "$port"
}

# ---------------------------------------------------------------------------
# Node: node-remove, status
# ---------------------------------------------------------------------------
cmd_node_remove() {
  require_root node-remove
  rm -f "$NGINX_ENABLED" "$NGINX_AVAIL"
  if command -v nginx >/dev/null 2>&1 && nginx -t >/dev/null 2>&1; then systemctl reload nginx || true; fi
  [[ -f "$ENV_FILE" ]] && env_write 0 "$(env_get HTTPS_PORT)" "$(env_get EXTRA_SANS)"
  systemctl restart stockpi-node.service 2>/dev/null || true
  success "HTTPS is off. HTTP is unchanged. Certificates were kept in $TLS_DIR (issue again to turn it back on)."
}

print_status() {
  local crt="$TLS_DIR/node.crt" enabled port
  enabled="$(env_get HTTPS_ENABLED)"; port="$(env_get HTTPS_PORT)"
  echo "" >&2
  if [[ "$enabled" == "1" && -f "$NGINX_ENABLED" ]]; then echo "  HTTPS:        on (port ${port:-443})" >&2
  else echo "  HTTPS:        off" >&2; fi
  if [[ ! -f "$crt" ]]; then echo "  Certificate:  none yet" >&2; echo "" >&2; return 0; fi
  local end sans ip
  end="$(openssl x509 -in "$crt" -noout -enddate | sed 's/^notAfter=//')"
  sans="$(openssl x509 -in "$crt" -noout -text | sed -n '/Subject Alternative Name/{n;p;}' | sed -e 's/IP Address:/IP:/g' | xargs)"
  ip="$(lan_ip)"
  echo "  Expires:      $end" >&2
  if ! openssl x509 -in "$crt" -noout -checkend $((30*86400)) >/dev/null; then
    echo -e "  ${YELLOW}Expires within 30 days: renew it.${NC}" >&2
  fi
  echo "  Covers:       $sans" >&2
  if [[ -n "$ip" && " ${sans//,/ } " != *" IP:$ip "* ]]; then
    echo -e "  ${YELLOW}This node's address ($ip) isn't covered: renew it.${NC}" >&2
  fi
  [[ -f "$TLS_DIR/ca.crt" ]] && echo "  CA:           $(openssl x509 -in "$TLS_DIR/ca.crt" -noout -fingerprint -sha256 | sed 's/^.*=//')" >&2
  echo "" >&2
}

cmd_status() { need_openssl; print_status; }

# ---------------------------------------------------------------------------
case "${1:-}" in
  ca-create)        shift; cmd_ca_create "$@" ;;
  sign)             shift; cmd_sign "$@" ;;
  issue|renew)      shift; cmd_issue "$@" ;;
  node-request)     shift; cmd_node_request "$@" ;;
  node-install)     shift; cmd_node_install "$@" ;;
  node-install-b64) shift; cmd_node_install_b64 "$@" ;;
  node-remove)      shift; cmd_node_remove "$@" ;;
  status)           shift; cmd_status "$@" ;;
  -h|--help|help|"") usage 0 ;;
  *) echo "Unknown command: $1" >&2; usage 1 ;;
esac
