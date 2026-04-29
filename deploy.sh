#!/bin/bash
# deploy.sh — WxAlert/SPCBot deployment script
# Portable version: Installs to current directory by default, runs as current user.

set -e

# Detect environment
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CURRENT_USER=$(whoami)
USER_HOME="$HOME"
if [ -n "$SUDO_USER" ]; then
    CURRENT_USER="$SUDO_USER"
    USER_HOME=$(getent passwd "$SUDO_USER" | cut -d: -f6)
fi

# Default to current directory if not specified
INSTALL_DIR="${INSTALL_DIR:-$SOURCE_DIR}"
SERVICE_USER="$CURRENT_USER"
SERVICE_NAME="spcbot"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
VENV_DIR="${INSTALL_DIR}/venv"
PYTHON_MIN_MAJOR=3
PYTHON_MIN_MINOR=10

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# ── Check Python version ──────────────────────────────────────────────────────
info "Checking Python version..."
PYTHON=$(command -v python3 || true)
[ -z "$PYTHON" ] && error "python3 not found. Please install Python 3.10 or newer."

PY_MAJOR=$($PYTHON -c "import sys; print(sys.version_info.major)")
PY_MINOR=$($PYTHON -c "import sys; print(sys.version_info.minor)")

if [ "$PY_MAJOR" -lt "$PYTHON_MIN_MAJOR" ] ||    { [ "$PY_MAJOR" -eq "$PYTHON_MIN_MAJOR" ] && [ "$PY_MINOR" -lt "$PYTHON_MIN_MINOR" ]; }; then
    error "Python 3.10+ required. Found: $PY_MAJOR.$PY_MINOR"
fi
info "Python $PY_MAJOR.$PY_MINOR found."

# ── Setup Install Directory ──────────────────────────────────────────────────
info "Installing to ${INSTALL_DIR}..."
mkdir -p "$INSTALL_DIR"

REAL_SOURCE="$(realpath "$SOURCE_DIR")"
REAL_INSTALL="$(realpath "$INSTALL_DIR")"

if [ "$REAL_SOURCE" != "$REAL_INSTALL" ]; then
    info "Copying files from ${SOURCE_DIR} to ${INSTALL_DIR}..."
    rsync -a \
        --exclude='venv/' \
        --exclude='cache/' \
        --exclude='*.log' \
        --exclude='*.log.*' \
        --exclude='.env' \
        --exclude='__pycache__/' \
        --exclude='*.pyc' \
        "${SOURCE_DIR}/" "${INSTALL_DIR}/"
    info "Files copied."
else
    info "Running deployment in-place at ${INSTALL_DIR}."
fi

# ── Virtual environment ───────────────────────────────────────────────────────
if [ -d "$VENV_DIR" ]; then
    if ! "${VENV_DIR}/bin/python" -c "import sys" &>/dev/null 2>&1; then
        warn "Existing venv is incompatible or broken — recreating..."
        rm -rf "$VENV_DIR"
    else
        info "Virtual environment OK."
    fi
fi

if [ ! -d "$VENV_DIR" ]; then
    info "Creating virtual environment..."
    $PYTHON -m venv "$VENV_DIR"
fi

info "Installing/updating dependencies..."
"${VENV_DIR}/bin/pip" install --upgrade pip --quiet
"${VENV_DIR}/bin/pip" install -r "${INSTALL_DIR}/requirements.txt" --quiet
info "Dependencies installed."

# ── Interactive .env setup ────────────────────────────────────────────────────
ENV_FILE="${INSTALL_DIR}/.env"
if [ -f "$ENV_FILE" ]; then
    warn ".env already exists — skipping required-field setup."
else
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  Bot Configuration"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    read -rsp "  Discord Bot Token: " DISCORD_TOKEN
    echo ""
    read -rp  "  SPC Channel ID:    " SPC_CHANNEL_ID
    read -rp  "  Models Channel ID: " MODELS_CHANNEL_ID
    read -rp  "  Guild ID:          " GUILD_ID
    echo ""

    cat > "$ENV_FILE" << EOF
# Required
DISCORD_TOKEN=${DISCORD_TOKEN}
SPC_CHANNEL_ID=${SPC_CHANNEL_ID}
MODELS_CHANNEL_ID=${MODELS_CHANNEL_ID}
GUILD_ID=${GUILD_ID}
EOF
    info ".env created."
fi

# ── Optional: Failover setup ──────────────────────────────────────────────────
if ! grep -q "^UPSTASH_REDIS_REST_URL=" "$ENV_FILE" 2>/dev/null; then
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  High Availability (optional)"
    echo "  Requires an Upstash Redis instance."
    echo "  Skip this for single-node installs."
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    read -rp "  Set up Primary/Standby failover? [y/N] " _setup_failover
    if [[ "$_setup_failover" =~ ^[Yy]$ ]]; then
        echo ""
        read -rp  "  Upstash Redis REST URL:   " _upstash_url
        read -rsp "  Upstash Redis REST Token: " _upstash_token
        echo ""
        read -rsp "  Shared failover secret:   " _failover_token
        echo ""
        read -rp  "  Your Discord User ID (for /failover): " _admin_id
        read -rp  "  Is this the Primary node? [Y/n] " _is_primary_input
        [[ "$_is_primary_input" =~ ^[Nn]$ ]] && _is_primary=false || _is_primary=true
        echo ""

        cat >> "$ENV_FILE" << EOF

# Failover — Upstash Redis + leader election
UPSTASH_REDIS_REST_URL=${_upstash_url}
UPSTASH_REDIS_REST_TOKEN=${_upstash_token}
FAILOVER_TOKEN=${_failover_token}
ADMIN_USER_ID=${_admin_id}
IS_PRIMARY=${_is_primary}
EOF
        info "Failover configuration written to .env."
    else
        info "Skipping failover setup — bot will run as a single node."
    fi
fi

# ── Optional: Syncthing setup ─────────────────────────────────────────────────
if ! grep -q "^SYNCTHING_API_KEY=" "$ENV_FILE" 2>/dev/null; then
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  Syncthing Events Archive Sync (optional)"
    echo "  Replicates events.db across nodes."
    echo "  Only useful alongside failover setup."
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    read -rp "  Set up Syncthing integration? [y/N] " _setup_syncthing
    if [[ "$_setup_syncthing" =~ ^[Yy]$ ]]; then
        echo ""
        read -rsp "  Syncthing API Key:  " _syncthing_key
        echo ""
        read -rp  "  Syncthing Folder ID: " _syncthing_folder
        echo ""

        cat >> "$ENV_FILE" << EOF

# Syncthing — events.db cross-node replication
SYNCTHING_API_KEY=${_syncthing_key}
SYNCTHING_FOLDER_ID=${_syncthing_folder}
EOF
        info "Syncthing configuration written to .env."
    else
        info "Skipping Syncthing setup."
    fi
fi

# ── Permissions ───────────────────────────────────────────────────────────────
info "Fixing runtime permissions for $SERVICE_USER..."
CACHE_DIR="${INSTALL_DIR}/cache"
mkdir -p "$CACHE_DIR"
mkdir -p "${INSTALL_DIR}/radar_data"
mkdir -p "${CACHE_DIR}/matplotlib"

# Ensure log file exists and is writable
touch "${INSTALL_DIR}/spc_bot.log"

# We only sudo for systemd and cloudflared; code ownership stays with CURRENT_USER
if [ "$EUID" -eq 0 ]; then
    chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR"
    chmod 600 "$ENV_FILE"
fi

# ── Systemd service ───────────────────────────────────────────────────────────
info "Configuring systemd service..."
sudo bash -c "cat > $SERVICE_FILE" << EOF
[Unit]
Description=WxAlert SPCBot Discord Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${INSTALL_DIR}
ExecStart=${VENV_DIR}/bin/python main.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
EnvironmentFile=${ENV_FILE}
Environment=MPLCONFIGDIR=${CACHE_DIR}/matplotlib

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"
info "Service installed and started as user '$SERVICE_USER'."

# ── Shell aliases ─────────────────────────────────────────────────────────────
ALIASES_FILE="${USER_HOME}/.bashrc"
if ! grep -q "# spcbot-aliases" "$ALIASES_FILE"; then
    info "Adding aliases to $ALIASES_FILE..."
    cat >> "$ALIASES_FILE" << 'ALIASES'

# spcbot-aliases
alias spcon='sudo systemctl start spcbot'
alias spcoff='sudo systemctl stop spcbot'
alias spcrestart='sudo systemctl restart spcbot'
alias spcstatus='systemctl status spcbot'
alias spclog='journalctl -u spcbot -f'
alias spclog50='journalctl -u spcbot -n 50'
alias spcupdate='git pull && ./deploy.sh'
ALIASES
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
info "Deployment complete! Bot running from ${INSTALL_DIR}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
