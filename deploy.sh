#!/bin/bash
# deploy.sh — WxAlert/SPCBot deployment script
# Installs to /opt/spc-bot, runs as dedicated non-root spcbot user,
# and adds shell aliases for easy management.

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

info()    { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# ── Checks ────────────────────────────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
    error "Please run with sudo: sudo ./deploy.sh"
fi

info "Checking Python version..."
PYTHON=$(command -v python3 || true)
if [ -z "$PYTHON" ]; then
    error "python3 not found. Please install Python 3.10 or newer."
fi

PY_MAJOR=$($PYTHON -c "import sys; print(sys.version_info.major)")
PY_MINOR=$($PYTHON -c "import sys; print(sys.version_info.minor)")

if [ "$PY_MAJOR" -lt "$PYTHON_MIN_MAJOR" ] ||    { [ "$PY_MAJOR" -eq "$PYTHON_MIN_MAJOR" ] && [ "$PY_MINOR" -lt "$PYTHON_MIN_MINOR" ]; }; then
    error "Python 3.10+ required. Found: $PY_MAJOR.$PY_MINOR"
fi
info "Python $PY_MAJOR.$PY_MINOR found."

# ── Dedicated service user ────────────────────────────────────────────────────
if ! id "$SERVICE_USER" &>/dev/null; then
    info "Creating system user '$SERVICE_USER'..."
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
else
    info "User '$SERVICE_USER' already exists."
fi

# ── Copy files to install directory ──────────────────────────────────────────
info "Installing to ${INSTALL_DIR}..."
mkdir -p "$INSTALL_DIR"

# Copy all bot files except venv, cache, logs, and .env
rsync -a --exclude='venv/' --exclude='cache/' --exclude='*.log*'     --exclude='.env' --exclude='__pycache__/' --exclude='*.pyc'     "${SOURCE_DIR}/" "${INSTALL_DIR}/"

info "Files copied to ${INSTALL_DIR}."

# ── Virtual environment ───────────────────────────────────────────────────────
if [ ! -d "$VENV_DIR" ]; then
    info "Creating virtual environment..."
    $PYTHON -m venv "$VENV_DIR"
else
    info "Virtual environment already exists."
fi

info "Installing dependencies..."
"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install -r "${INSTALL_DIR}/requirements.txt" --quiet
info "Dependencies installed."

# ── Interactive .env setup ────────────────────────────────────────────────────
ENV_FILE="${INSTALL_DIR}/.env"
if [ -f "$ENV_FILE" ]; then
    warn ".env already exists. Skipping interactive setup."
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

# ── File permissions ──────────────────────────────────────────────────────────
info "Setting permissions..."
chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR"
chmod 600 "$ENV_FILE"

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

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

# ── Shell aliases ─────────────────────────────────────────────────────────────
info "Installing shell aliases..."
ALIASES_FILE="/etc/profile.d/spcbot-aliases.sh"
cat > "$ALIASES_FILE" << 'ALIASES'
# WxAlert SPCBot management aliases
alias spcon='sudo systemctl start spcbot'
alias spcoff='sudo systemctl stop spcbot'
alias spcrestart='sudo systemctl restart spcbot'
alias spcstatus='systemctl status spcbot'
alias spclog='journalctl -u spcbot -f'
alias spclog50='journalctl -u spcbot -n 50'
ALIASES
chmod 644 "$ALIASES_FILE"
info "Aliases installed. Run 'source /etc/profile.d/spcbot-aliases.sh' or open a new shell to use them."

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
info "Deployment complete! Bot installed to ${INSTALL_DIR}"
echo ""
echo "  Available commands:"
echo "  spcon        — start the bot"
echo "  spcoff       — stop the bot"
echo "  spcrestart   — restart the bot"
echo "  spcstatus    — show bot status"
echo "  spclog       — follow live logs"
echo "  spclog50     — show last 50 log lines"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
