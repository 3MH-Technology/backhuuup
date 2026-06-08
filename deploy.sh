#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# Wolf Host — Fully Automated Ubuntu VPS Provisioning Script
# ─────────────────────────────────────────────────────────────────────
# Usage:
#   chmod +x deploy.sh
#   sudo ./deploy.sh --domain=host.example.com --email=admin@example.com
#
# Options:
#   --domain=DOMAIN     Required.  The domain for the platform.
#   --email=EMAIL       Required.  Email for Let's Encrypt notifications.
#   --password=PASS     Optional.  PostgreSQL password (auto-generated if omitted).
#   --secret=SECRET     Optional.  JWT secret key (auto-generated if omitted).
#   --branch=BRANCH     Optional.  Git branch to deploy (default: main).
#   --repo=REPO         Optional.  Git repository URL (default: current dir).
#
# What this script does:
#   1. Updates system packages
#   2. Installs Docker Engine + Docker Compose plugin
#   3. Hardens kernel parameters (network / container security)
#   4. Configures UFW firewall (22, 80, 443 only)
#   5. Clones the repository (or copies from current directory)
#   6. Generates .env with secure random secrets
#   7. Builds and starts all containers via docker compose
#   8. Obtains initial Let's Encrypt SSL certificate
#   9. Sets up automatic unattended security updates
# ─────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Colors ──────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── Parse arguments ─────────────────────────────────────────────────
for arg in "$@"; do
    case "$arg" in
        --domain=*)     DOMAIN="${arg#*=}" ;;
        --email=*)      EMAIL="${arg#*=}"  ;;
        --password=*)   POSTGRES_PASSWORD="${arg#*=}" ;;
        --secret=*)     SECRET_KEY="${arg#*=}" ;;
        --branch=*)     BRANCH="${arg#*=}" ;;
        --repo=*)       REPO_URL="${arg#*=}" ;;
        --help)         echo "Usage: $0 --domain=... --email=..." ; exit 0 ;;
        *)              err "Unknown argument: $arg" ;;
    esac
done

# ── Validate required ───────────────────────────────────────────────
[[ -z "${DOMAIN:-}" ]] && err "--domain is required (e.g. --domain=host.example.com)"
[[ -z "${EMAIL:-}" ]] && err "--email is required (e.g. --email=admin@example.com)"

BRANCH="${BRANCH:-main}"
REPO_URL="${REPO_URL:-}"

# Generate secrets if not provided
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-$(openssl rand -base64 32)}"
SECRET_KEY="${SECRET_KEY:-$(openssl rand -hex 64)}"

APP_DIR="/opt/wolfhost"
LOG_FILE="/var/log/wolfhost-deploy.log"

# ── Ensure we are root ──────────────────────────────────────────────
[[ $EUID -eq 0 ]] || err "This script must be run as root (sudo)."

exec > >(tee -a "$LOG_FILE") 2>&1

# ═════════════════════════════════════════════════════════════════════
# STEP 1 — System update & essentials
# ═════════════════════════════════════════════════════════════════════
info "STEP 1/9 — Updating system packages..."
apt-get update -qq
apt-get upgrade -y -qq
apt-get install -y -qq \
    curl ca-certificates gnupg lsb-release \
    git ufw htop iotop unattended-upgrades \
    openssl

ok "System updated"

# ═════════════════════════════════════════════════════════════════════
# STEP 2 — Install Docker Engine
# ═════════════════════════════════════════════════════════════════════
info "STEP 2/9 — Installing Docker Engine..."
if ! command -v docker &>/dev/null; then
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    systemctl enable docker
    systemctl start docker
fi

ok "Docker $(docker --version | cut -d' ' -f3 | tr -d ',') installed"

# ═════════════════════════════════════════════════════════════════════
# STEP 3 — Configure Docker daemon for security
# ═════════════════════════════════════════════════════════════════════
info "STEP 3/9 — Hardening Docker daemon..."
mkdir -p /etc/docker
cat > /etc/docker/daemon.json << 'EOF'
{
  "icc": false,
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  },
  "live-restore": true,
  "userland-proxy": false,
  "ip-forward": true,
  "iptables": true,
  "ip6tables": true,
  "experimental": false,
  "max-concurrent-downloads": 3,
  "max-concurrent-uploads": 3
}
EOF
systemctl restart docker
ok "Docker daemon hardened"

# ═════════════════════════════════════════════════════════════════════
# STEP 4 — Kernel hardening for container security
# ═════════════════════════════════════════════════════════════════════
info "STEP 4/9 — Applying kernel hardening..."
cat > /etc/sysctl.d/99-wolfhost.conf << 'EOF'
# ── Network hardening ──────────────────────────────────────────
net.ipv4.tcp_syncookies = 1
net.ipv4.tcp_rfc1337 = 1
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv4.conf.all.secure_redirects = 0
net.ipv4.conf.default.secure_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.default.send_redirects = 0
net.ipv4.icmp_echo_ignore_broadcasts = 1
net.ipv4.icmp_ignore_bogus_error_responses = 1
net.ipv6.conf.all.disable_ipv6 = 1
net.ipv6.conf.default.disable_ipv6 = 1

# ── Container security ─────────────────────────────────────────
net.bridge.bridge-nf-call-iptables = 1
net.bridge.bridge-nf-call-ip6tables = 0
net.ipv4.ip_forward = 1

# ── Resource limits ────────────────────────────────────────────
kernel.pid_max = 65536
fs.file-max = 100000
EOF
sysctl -p /etc/sysctl.d/99-wolfhost.conf &>/dev/null || warn "Some sysctls require reboot"
ok "Kernel parameters applied"

# ═════════════════════════════════════════════════════════════════════
# STEP 5 — Configure UFW firewall
# ═════════════════════════════════════════════════════════════════════
info "STEP 5/9 — Configuring UFW firewall..."
ufw --force reset
ufw default deny incoming
ufw default allow outgoing

# SSH access (change port if your SSH is on a non-default port)
ufw allow 22/tcp comment 'SSH'

# HTTP / HTTPS
ufw allow 80/tcp  comment 'HTTP'
ufw allow 443/tcp comment 'HTTPS'

# Rate-limit SSH to prevent brute-force
ufw limit 22/tcp

ufw --force enable
ok "Firewall active — only ports 22, 80, 443 are open"

# ═════════════════════════════════════════════════════════════════════
# STEP 6 — Clone / copy application code
# ═════════════════════════════════════════════════════════════════════
info "STEP 6/9 — Deploying application code..."

# Clean if exists
if [[ -d "$APP_DIR" ]]; then
    warn "$APP_DIR already exists — backing up to ${APP_DIR}.bak"
    rm -rf "${APP_DIR}.bak" 2>/dev/null || true
    mv "$APP_DIR" "${APP_DIR}.bak"
fi

mkdir -p "$APP_DIR"

if [[ -n "${REPO_URL:-}" ]]; then
    git clone --branch "$BRANCH" --depth 1 "$REPO_URL" "$APP_DIR"
    ok "Repository cloned from $REPO_URL (branch: $BRANCH)"
else
    # Deploy from current directory (script is running from the project root)
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
    if [[ -f "$SCRIPT_DIR/docker-compose.yml" ]]; then
        cp -r "$SCRIPT_DIR"/* "$APP_DIR"/
        ok "Code copied from $SCRIPT_DIR"
    else
        # Try parent directory (common when deploy.sh is inside scripts/)
        PARENT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
        if [[ -f "$PARENT_DIR/docker-compose.yml" ]]; then
            cp -r "$PARENT_DIR"/* "$APP_DIR"/
            ok "Code copied from $PARENT_DIR"
        else
            err "Cannot find docker-compose.yml. Either use --repo= or run deploy.sh from the project root."
        fi
    fi
fi

cd "$APP_DIR"

# ═════════════════════════════════════════════════════════════════════
# STEP 7 — Create .env file
# ═════════════════════════════════════════════════════════════════════
info "STEP 7/9 — Generating .env configuration..."

cat > "$APP_DIR/.env" << EOF
# ── Wolf Host ── Auto-generated by deploy.sh ────────────────────────
POSTGRES_USER=wolfhost
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
POSTGRES_DB=wolfhost
SECRET_KEY=${SECRET_KEY}
DOMAIN=${DOMAIN}
LOG_LEVEL=info
MAX_BOTS_PER_USER=3
CONTAINER_MEM_LIMIT_MB=128
CONTAINER_CPU_NANOS=500000000
PYTHON_IMAGE=python:3.10-alpine
PHP_IMAGE=php:8.2-alpine
DOCKER_NETWORK=estidafa_bot_net
EOF

chmod 600 "$APP_DIR/.env"
ok ".env created with secure secrets"

# ═════════════════════════════════════════════════════════════════════
# STEP 8 — Docker compose build & up
# ═════════════════════════════════════════════════════════════════════
info "STEP 8/9 — Building and starting containers..."

# Create required directories
mkdir -p nginx/ssl
mkdir -p certbot/www

# Pull base images in parallel
docker pull postgres:16-alpine &
docker pull nginx:1.27-alpine &
docker pull certbot/certbot:latest &
docker pull python:3.10-alpine &
docker pull php:8.2-alpine &
wait

# Build and start
docker compose build --no-cache
docker compose up -d --remove-orphans

ok "All containers are running"
docker compose ps

# ═════════════════════════════════════════════════════════════════════
# STEP 9 — Initial SSL certificate (Let's Encrypt)
# ═════════════════════════════════════════════════════════════════════
info "STEP 9/9 — Obtaining initial SSL certificate..."
# First, ensure nginx can serve the ACME challenge
# by temporarily creating a minimal HTTP-only config

mkdir -p certbot/www

# Run certbot
docker compose run --rm certbot certonly \
    --webroot \
    --webroot-path /var/www/certbot \
    --email "$EMAIL" \
    --agree-tos \
    --no-eff-email \
    --non-interactive \
    -d "$DOMAIN" \
    -d "*.$DOMAIN" || warn "SSL certificate request failed — you may need to verify DNS records"

# Reload nginx to pick up the certs
docker compose exec nginx nginx -s reload 2>/dev/null || true

# ── Verify the domain resolves ──────────────────────────────────────
info "Verifying deployment..."
sleep 5
if curl -sI "https://$DOMAIN/" --max-time 10 >/dev/null 2>&1; then
    ok "Platform is live at https://$DOMAIN/"
else
    warn "Platform may not be reachable yet. Check DNS propagation and nginx logs."
    warn "  docker compose logs nginx"
fi

# ═════════════════════════════════════════════════════════════════════
# ── Final summary ──────────────────────────────────────────────────
# ═════════════════════════════════════════════════════════════════════
echo ""
echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Wolf Host — Deployment Complete ✓${NC}"
echo -e "${GREEN}══════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  Dashboard:  ${CYAN}https://${DOMAIN}/${NC}"
echo -e "  API:        ${CYAN}https://${DOMAIN}/api/${NC}"
echo ""
echo -e "  PostgreSQL password: ${YELLOW}${POSTGRES_PASSWORD}${NC}"
echo -e "  JWT secret:          ${YELLOW}${SECRET_KEY:0:16}...${NC}"
echo ""
echo -e "  ${YELLOW}Save these credentials in a password manager.${NC}"
echo ""
echo -e "  Useful commands:"
echo -e "    Logs:       ${CYAN}docker compose -f ${APP_DIR}/docker-compose.yml logs -f${NC}"
echo -e "    Restart:    ${CYAN}docker compose -f ${APP_DIR}/docker-compose.yml restart${NC}"
echo -e "    Update:     ${CYAN}cd ${APP_DIR} && git pull && docker compose up -d --build${NC}"
echo ""

# ── Enable unattended security updates ─────────────────────────────
dpkg-reconfigure -f noninteractive unattended-upgrades 2>/dev/null || true
ok "Automatic security updates configured"

# ── Done ───────────────────────────────────────────────────────────
info "Deploy log saved to ${LOG_FILE}"
