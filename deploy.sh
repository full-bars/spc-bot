#!/bin/bash
# deploy.sh — WxAlert/SPCBot deployment script
# Installs to /opt/spc-bot, runs as dedicated non-root spcbot user.
# Safe to run multiple times (idempotent).

set -e

INSTALL_DIR="/opt/spc-bot"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_USER="spcbot"
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

# ── Must run as root ──────────────────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
    error "Please run with sudo: sudo ./deploy.sh"
fi

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

# ── Create service user ───────────────────────────────────────────────────────
if ! id "$SERVICE_USER" &>/dev/null; then
    info "Creating system user '$SERVICE_USER'..."
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
else
    info "User '$SERVICE_USER' already exists."
fi

# ── Copy files to install directory ──────────────────────────────────────────
info "Installing to ${INSTALL_DIR}..."
mkdir -p "$INSTALL_DIR"

REAL_SOURCE="$(realpath "$SOURCE_DIR")"
REAL_INSTALL="$(realpath "$INSTALL_DIR")"

if [ "$REAL_SOURCE" != "$REAL_INSTALL" ]; then
    info "Copying files from ${SOURCE_DIR} to ${INSTALL_DIR}..."
    rsync -a         --exclude='venv/'         --exclude='cache/'         --exclude='*.log'         --exclude='*.log.*'         --exclude='.env'         --exclude='__pycache__/'         --exclude='*.pyc'         "${SOURCE_DIR}/" "${INSTALL_DIR}/"
    info "Files copied."
else
    info "Already installed at ${INSTALL_DIR} — skipping file copy."
fi

# ── Virtual environment ───────────────────────────────────────────────────────
# Detect and remove incompatible venv (wrong arch or broken)
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

# ── Install cloudflared (for failover tunnel) ───────────────────────────────
if ! command -v cloudflared &>/dev/null; then
    info "Installing cloudflared..."
    ARCH=$(uname -m)
    case "$ARCH" in
        x86_64)  CF_ARCH="amd64" ;;
        aarch64) CF_ARCH="arm64" ;;
        armv7l)  CF_ARCH="arm" ;;
        *)       warn "Unknown arch $ARCH — skipping cloudflared install" ; CF_ARCH="" ;;
    esac
    if [ -n "$CF_ARCH" ]; then
        curl -fsSL "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${CF_ARCH}" -o /tmp/cloudflared
        chmod +x /tmp/cloudflared
        mv /tmp/cloudflared /usr/local/bin/cloudflared
        info "cloudflared installed ($CF_ARCH)."
    fi
else
    info "cloudflared already installed."
fi

# ── Interactive .env setup ────────────────────────────────────────────────────
ENV_FILE="${INSTALL_DIR}/.env"
if [ -f "$ENV_FILE" ]; then
    warn ".env already exists — skipping interactive setup."
    warn "Edit ${ENV_FILE} manually if you need to change values."
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
# Required — no defaults, bot will not start without these
SPC_CHANNEL_ID=${SPC_CHANNEL_ID}
MODELS_CHANNEL_ID=${MODELS_CHANNEL_ID}
GUILD_ID=${GUILD_ID}
# Optional — these have sensible defaults
# CACHE_DIR=cache
# LOG_FILE=spc_bot.log
# MANUAL_CACHE_FILE=posted_records.json
# AUTO_CACHE_FILE=auto_posted_records.json
EOF
    info ".env created."
fi

# ── Permissions ───────────────────────────────────────────────────────────────
# Root owns the code so any admin can git pull/push
# spcbot only owns what it needs to write at runtime
info "Setting permissions..."
CACHE_DIR="${INSTALL_DIR}/cache"
mkdir -p "$CACHE_DIR"

chown root:root "$INSTALL_DIR"
chown -R root:root "${INSTALL_DIR}"
chown -R "$SERVICE_USER":"$SERVICE_USER" "$CACHE_DIR"
chown "$SERVICE_USER":"$SERVICE_USER" "$ENV_FILE"
chmod 600 "$ENV_FILE"
chmod -R a+rX "$INSTALL_DIR"
# venv needs to be executable by spcbot
chown -R "$SERVICE_USER":"$SERVICE_USER" "$VENV_DIR"

# Create and fix permissions for runtime files
touch "${INSTALL_DIR}/spc_bot.log"
chown "$SERVICE_USER":"$SERVICE_USER" "${INSTALL_DIR}/spc_bot.log"
    chown "$SERVICE_USER":"$SERVICE_USER" "${INSTALL_DIR}/radar_data"
    chmod 775 "${INSTALL_DIR}"
    chown root:"$SERVICE_USER" "${INSTALL_DIR}"
mkdir -p "${CACHE_DIR}/matplotlib"
chown -R "$SERVICE_USER":"$SERVICE_USER" "${CACHE_DIR}/matplotlib"

# ── Git safe directory for root ───────────────────────────────────────────────
git config --global --add safe.directory "$INSTALL_DIR" 2>/dev/null || true
info "Git safe directory configured."

# ── Systemd service ───────────────────────────────────────────────────────────
info "Installing systemd service..."
cat > "$SERVICE_FILE" << EOF
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
Environment=MPLCONFIGDIR=/opt/spc-bot/cache/matplotlib

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"
info "Service installed and started."

# ── Shell aliases (system-wide, works for all shells) ─────────────────────────
info "Installing shell aliases..."
cat > /etc/bash.bashrc.d/spcbot 2>/dev/null || {
    # Fallback: append to /etc/bash.bashrc if .d directory doesn't exist
    ALIASES_MARKER="# spcbot-aliases"
    if ! grep -q "$ALIASES_MARKER" /etc/bash.bashrc; then
        cat >> /etc/bash.bashrc << 'ALIASES'
# spcbot-aliases
alias spcon='sudo systemctl start spcbot'
alias spcoff='sudo systemctl stop spcbot'
alias spcrestart='sudo systemctl restart spcbot'
alias spcstatus='systemctl status spcbot'
alias spclog='journalctl -u spcbot -f'
alias spclog50='journalctl -u spcbot -n 50'
alias spcupdate='sudo git -C /opt/spc-bot pull && sudo systemctl restart spcbot && echo "Bot updated and restarted."'
ALIASES
    fi
}

info "Aliases added to /etc/bash.bashrc — open a new shell or run: source /etc/bash.bashrc"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
info "Deployment complete! Bot installed to ${INSTALL_DIR}"
echo ""
echo "  NOTE: You may need to log out and back in for aliases to take effect."
echo "  Or run: source /etc/bash.bashrc"
echo ""
echo "  Then use:"
echo "  spcon        — start the bot"
echo "  spcoff       — stop the bot"
echo "  spcrestart   — restart the bot"
echo "  spcstatus    — show bot status"
echo "  spclog       — follow live logs"
echo "  spclog50     — show last 50 log lines"
echo "  spcupdate    — pull latest code and restart"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
