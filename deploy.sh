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

# ── Redis replication helper (standby nodes only) ─────────────────────────────
# Configures this Redis instance as a replica of the primary and persists the
# setting to redis.conf so it survives Redis restarts. Safe to call on re-runs;
# exits early if replication is already correctly established.
_configure_redis_replication() {
    local primary_url="$1"
    local primary_host primary_port

    # Parse redis://host:port[/db]
    primary_host=$(echo "$primary_url" | sed -E 's|redis://([^:/]+).*|\1|')
    primary_port=$(echo "$primary_url" | sed -E 's|redis://[^:]+:([0-9]+).*|\1|')
    primary_port=${primary_port:-6379}

    if ! command -v redis-cli &>/dev/null; then
        warn "redis-cli not found — skipping Redis replication setup."
        warn "Manually run: redis-cli REPLICAOF ${primary_host} ${primary_port}"
        return
    fi

    # Check if already correctly wired up
    local cur_role cur_host cur_port
    cur_role=$(redis-cli info replication 2>/dev/null | grep "^role:"        | cut -d: -f2 | tr -d '[:space:]\r')
    cur_host=$(redis-cli info replication 2>/dev/null | grep "^master_host:" | cut -d: -f2 | tr -d '[:space:]\r')
    cur_port=$(redis-cli info replication 2>/dev/null | grep "^master_port:" | cut -d: -f2 | tr -d '[:space:]\r')

    if [[ "$cur_role" == "slave" && "$cur_host" == "$primary_host" && "$cur_port" == "$primary_port" ]]; then
        local link_status
        link_status=$(redis-cli info replication 2>/dev/null | grep "^master_link_status:" | cut -d: -f2 | tr -d '[:space:]\r')
        info "Redis replication already active (${primary_host}:${primary_port}, link=${link_status})."
        return
    fi

    info "Configuring Redis replication → ${primary_host}:${primary_port}..."
    if ! redis-cli REPLICAOF "$primary_host" "$primary_port" 2>/dev/null | grep -q "OK"; then
        warn "redis-cli REPLICAOF failed — is Redis running? May need: sudo systemctl start redis-server"
        warn "Once Redis is up, run: redis-cli REPLICAOF ${primary_host} ${primary_port}"
        return
    fi
    info "Redis replication established (runtime)."

    # Persist to redis.conf so it survives Redis restarts
    local redis_conf
    redis_conf=$(redis-cli info server 2>/dev/null | grep "^config_file:" | cut -d: -f2- | tr -d '[:space:]\r')
    if [[ -n "$redis_conf" && -f "$redis_conf" ]]; then
        if sudo grep -qE "^(replicaof|slaveof) " "$redis_conf" 2>/dev/null; then
            sudo sed -i "s|^replicaof .*|replicaof ${primary_host} ${primary_port}|; \
                         s|^slaveof .*|replicaof ${primary_host} ${primary_port}|" "$redis_conf"
        else
            echo "replicaof ${primary_host} ${primary_port}" | sudo tee -a "$redis_conf" > /dev/null
        fi
        info "Redis replication persisted to ${redis_conf}."
    else
        warn "Could not locate redis.conf — replication is active but won't survive a Redis restart."
        warn "Add this line to your redis.conf: replicaof ${primary_host} ${primary_port}"
    fi

    # Verify link came up
    sleep 2
    local link_status
    link_status=$(redis-cli info replication 2>/dev/null | grep "^master_link_status:" | cut -d: -f2 | tr -d '[:space:]\r')
    if [[ "$link_status" == "up" ]]; then
        info "Redis replication verified: link is UP."
    else
        warn "Redis link status: '${link_status:-unknown}'. Check connectivity to ${primary_host}:${primary_port}."
    fi
}

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

# ── Rust extension (spc_rust_core) ──────────────────────────────────────────────
# pyproject.toml uses the maturin build backend, but `pip install -r
# requirements.txt` does NOT build it — so without this step a `git pull` that
# changes Rust code would ship a stale extension. Build + install via PEP 517
# (pip pulls maturin into an isolated build env); only needs cargo on PATH.
if [ -f "${INSTALL_DIR}/Cargo.toml" ]; then
    if command -v cargo >/dev/null 2>&1; then
        info "Building Rust extension (spc_rust_core) with $(cargo --version)..."
        "${VENV_DIR}/bin/pip" install "${INSTALL_DIR}" --quiet
        info "Rust extension built and installed."
    else
        warn "cargo not found on PATH — skipping Rust build; spc_rust_core may be stale."
        warn "Install Rust (https://rustup.rs) and ensure cargo is on PATH (e.g. /usr/local/bin or ~/.cargo/bin)."
    fi
fi

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
# Skip if IS_PRIMARY is already set (i.e., failover is already configured)
if ! grep -q "^IS_PRIMARY=" "$ENV_FILE" 2>/dev/null; then
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  High Availability (optional)"
    echo "  Requires a local Redis instance."
    echo "  Skip this for single-node installs."
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    read -rp "  Set up Primary/Standby failover? [y/N] " _setup_failover
    if [[ "$_setup_failover" =~ ^[Yy]$ ]]; then
        echo ""
        read -rp  "  Redis host (default: localhost):     " _redis_host
        _redis_host=${_redis_host:-localhost}
        read -rp  "  Redis port (default: 6379):         " _redis_port
        _redis_port=${_redis_port:-6379}
        echo ""
        read -rsp "  Shared failover secret:   " _failover_token
        echo ""
        read -rp  "  Your Discord User ID (for /failover): " _admin_id
        read -rp  "  Is this the Primary node? [Y/n] " _is_primary_input
        [[ "$_is_primary_input" =~ ^[Nn]$ ]] && _is_primary=false || _is_primary=true

        _election_redis_url=""
        if [[ "$_is_primary" == "false" ]]; then
            echo ""
            echo "  Standby nodes need to reach the Primary's Redis for leader election."
            echo "  Enter the Primary node's Redis URL (e.g. via Tailscale)."
            read -rp  "  Primary Redis URL (e.g. redis://100.x.x.x:6379/0): " _election_redis_url
        fi
        echo ""

        cat >> "$ENV_FILE" << EOF

# Failover — Local Redis + leader election
REDIS_HOST=${_redis_host}
REDIS_PORT=${_redis_port}
FAILOVER_TOKEN=${_failover_token}
ADMIN_USER_ID=${_admin_id}
IS_PRIMARY=${_is_primary}
EOF
        if [[ -n "$_election_redis_url" ]]; then
            cat >> "$ENV_FILE" << EOF
# Standby: election traffic points at the Primary's Redis (via Tailscale)
ELECTION_REDIS_URL=${_election_redis_url}
EOF
        fi
        info "Failover configuration written to .env."

        # Configure Redis replication on standby nodes
        if [[ "$_is_primary" == "false" && -n "$_election_redis_url" ]]; then
            _configure_redis_replication "$_election_redis_url"
        fi
    else
        info "Skipping failover setup — bot will run as a single node."
    fi
else
    info "Failover already configured (IS_PRIMARY found). Skipping setup prompts."
    # On re-runs (e.g. spcupdate), still verify Redis replication is live on standby nodes.
    _existing_is_primary=$(grep "^IS_PRIMARY=" "$ENV_FILE" | cut -d= -f2 | tr -d '[:space:]')
    _existing_election_url=$(grep "^ELECTION_REDIS_URL=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '[:space:]')
    if [[ "$_existing_is_primary" == "false" && -n "$_existing_election_url" ]]; then
        _configure_redis_replication "$_existing_election_url"
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

# ── Log rotation configuration ────────────────────────────────────────────────
if [ "$EUID" -eq 0 ] && command -v logrotate &>/dev/null; then
    info "Installing logrotate configuration..."
    sudo cp "${INSTALL_DIR}/config/logrotate.conf" "/etc/logrotate.d/${SERVICE_NAME}"
    sudo chmod 644 "/etc/logrotate.d/${SERVICE_NAME}"
    info "Logrotate installed: 50 MB per file, 12 files (90+ days), gzip -9 compression."
else
    if [ "$EUID" -ne 0 ]; then
        warn "Logrotate setup requires sudo. Skipping automated log rotation."
        info "To set up manually, run: sudo cp config/logrotate.conf /etc/logrotate.d/spcbot"
    else
        info "logrotate not installed. Logs will grow unbounded."
    fi
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

# Verify the service actually came up
sleep 3
if systemctl is-active --quiet "$SERVICE_NAME"; then
    info "Service is running."
else
    warn "Service failed to start. Check logs: journalctl -u ${SERVICE_NAME} -n 30"
fi

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
