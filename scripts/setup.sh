#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# Investment Assistant — Full Raspberry Pi 5 Setup Script
# Run as the non-root user (e.g. `pi`):
#   bash scripts/setup.sh
# Optional: run the validated deployment at the end:
#   bash scripts/setup.sh --deploy
# ══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

PI_HOME="$HOME"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOY_AFTER_SETUP=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --deploy)
      DEPLOY_AFTER_SETUP=1
      shift
      ;;
    -h|--help)
      echo "Usage: bash scripts/setup.sh [--deploy]"
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      echo "Usage: bash scripts/setup.sh [--deploy]" >&2
      exit 1
      ;;
  esac
done

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║   Investment Assistant – Pi 5 Setup                 ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# ── 1. System update ──────────────────────────────────────────────────────────
echo "[1/9] Updating system packages…"
sudo apt-get update -q
sudo apt-get upgrade -y -q
sudo apt-get install -y -q \
  ca-certificates \
  curl \
  iptables-persistent \
  netfilter-persistent \
  openssl \
  python3 \
  python3-pip \
  python3-venv \
  rsync \
  ufw

# ── 2. Install Docker ─────────────────────────────────────────────────────────
echo "[2/9] Installing Docker…"
if ! command -v docker &>/dev/null; then
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER"
  echo "Docker installed. NOTE: log out and back in for docker group to take effect."
else
  echo "Docker already installed: $(docker --version)"
fi

# ── 3. Install WireGuard ──────────────────────────────────────────────────────
echo "[3/9] Installing WireGuard…"
sudo apt-get install -y -q wireguard wireguard-tools qrencode

# This deployment is a host-only VPN: clients reach the Pi's 10.8.0.1
# services, but the Pi is not configured as an internet gateway.

# ── 4. Configure WireGuard ────────────────────────────────────────────────────
echo "[4/9] Setting up WireGuard keys…"
if [[ ! -f /etc/wireguard/server_private.key ]]; then
  sudo mkdir -p /etc/wireguard
  sudo chmod 700 /etc/wireguard
  # Generate server key pair
  sudo bash -c 'umask 077; wg genkey | tee /etc/wireguard/server_private.key | wg pubkey > /etc/wireguard/server_public.key'
  # Generate pre-shared key (extra symmetric layer on top of public-key auth)
  sudo bash -c 'umask 077; wg genpsk > /etc/wireguard/preshared.key'
  SERVER_PUBKEY=$(sudo cat /etc/wireguard/server_public.key)
  echo ""
  echo "┌─────────────────────────────────────────────────────┐"
  echo "  WireGuard server public key:"
  echo "  $SERVER_PUBKEY"
  echo ""
  echo "  A pre-shared key was generated at:"
  echo "  /etc/wireguard/preshared.key"
  echo "  Include it in each [Peer] block as:"
  echo "  PresharedKey = <contents of preshared.key>"
  echo "└─────────────────────────────────────────────────────┘"
  echo ""
  echo "Copy $PROJECT_DIR/config/wireguard/wg0.conf.template"
  echo "to /etc/wireguard/wg0.conf and fill in the keys."
else
  echo "WireGuard keys already exist."
fi

# ── 5. Set up project .env ────────────────────────────────────────────────────
echo "[5/9] Configuring environment…"
cd "$PROJECT_DIR"
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo ""
  echo "┌──────────────────────────────────────────────────────┐"
  echo "  .env created — fill in required fields:"
  echo "  POSTGRES_PASSWORD, PIHOLE_PASSWORD, and browser auth values"
  echo "  LLM_MODEL_PATH (after downloading a model),"
  echo "  brokerage API keys (ALPACA, COINBASE, BINANCE, ...)"
  echo "└──────────────────────────────────────────────────────┘"
  echo ""
else
  echo ".env already exists — skipping."
fi

# ── 6. Generate TLS certificate ───────────────────────────────────────────────
echo "[6/9] Generating self-signed TLS certificate…"
mkdir -p "$PROJECT_DIR/config/nginx/certs"
CERT="$PROJECT_DIR/config/nginx/certs/selfsigned.crt"
KEY="$PROJECT_DIR/config/nginx/certs/selfsigned.key"
if [[ ! -f "$CERT" ]]; then
  openssl req -x509 -nodes -days 3650 -newkey rsa:4096 \
    -keyout "$KEY" -out "$CERT" \
    -subj "/C=PT/ST=Lisbon/L=Lisbon/O=InvestmentAssistant/CN=investment-assistant" \
    -quiet
  chmod 600 "$KEY"
  echo "TLS cert generated."
else
  echo "TLS cert already exists."
fi

# ── 7. Download a model ───────────────────────────────────────────────────────
echo "[7/9] Checking for LLM model…"
mkdir -p "$PROJECT_DIR/models"
if [[ -z "$(ls -A "$PROJECT_DIR/models" 2>/dev/null)" ]]; then
  echo ""
  echo "  No model found in $PROJECT_DIR/models/"
  echo "  Download one now with:"
  echo "    python $PROJECT_DIR/scripts/download_model.py --list"
  echo "    python $PROJECT_DIR/scripts/download_model.py qwen2.5-7b \\"
  echo "      --output-dir $PROJECT_DIR/models"
  echo ""
fi

# ── 8. Deployment readiness ───────────────────────────────────────────────────
echo "[8/9] Deployment readiness check…"
cd "$PROJECT_DIR"
echo ""
echo "  Base Pi setup is ready."
echo "  After you edit .env and download a GGUF model, deploy with:"
echo "    bash scripts/pi_deploy.sh"
echo ""
if [[ "$DEPLOY_AFTER_SETUP" -eq 1 ]]; then
  echo "  --deploy requested; deployment will run after firewall setup."
fi

# ── 9. Firewall ────────────────────────────────────────────────────────────────
echo "[9/9] Configuring firewall…"

# This profile is a VPN endpoint, not a router.  Explicitly turn forwarding off
# so a previous Pi networking setup cannot silently make the host a transit
# gateway for a WireGuard peer.
echo "net.ipv4.ip_forward=0" | sudo tee /etc/sysctl.d/99-investment-assistant-host-only.conf >/dev/null
echo "net.ipv6.conf.all.forwarding=0" | sudo tee -a /etc/sysctl.d/99-investment-assistant-host-only.conf >/dev/null
sudo sysctl --system >/dev/null

# ── UFW (host-level firewall) ─────────────────────────────────────────────────
# IMPORTANT: Ports 80 and 443 are intentionally NOT opened here.
# The investment assistant is only accessible through the WireGuard VPN (10.8.0.0/24).
# Do NOT forward ports 80/443 on your router — only forward UDP 51820.
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 51820/udp                      # WireGuard — THE ONLY INTERNET-FACING PORT
sudo ufw allow from 10.8.0.0/24 to any port 22 # SSH administration via VPN only
sudo ufw allow from 10.8.0.0/24   to any port 53    # Pi-hole DNS (VPN clients)

# ── DOCKER-USER iptables chain ────────────────────────────────────────────────
# Docker bypasses UFW by writing its own iptables rules.
# The DOCKER-USER chain is evaluated BEFORE Docker's own rules and is never
# overwritten by Docker.  Rules here survive `docker compose restart`.
#
# Strategy: only allow web and Pi-hole DNS traffic from the WireGuard VPN subnet.
# Everything else (i.e. the internet) is dropped, even if Docker binds to 0.0.0.0.

# Flush any existing DOCKER-USER rules first
sudo iptables -F DOCKER-USER 2>/dev/null || true

# Allow VPN clients (10.8.0.0/24) to reach web ports
sudo iptables -I DOCKER-USER 1 -p tcp -m multiport --dports 80,443 \
    -s 10.8.0.0/24 -j ACCEPT
sudo iptables -I DOCKER-USER 2 -p udp --dport 53 -s 10.8.0.0/24 -j ACCEPT
sudo iptables -I DOCKER-USER 3 -p tcp --dport 53 -s 10.8.0.0/24 -j ACCEPT
# DROP everything else (internet) — this is the line that matters
sudo iptables -A DOCKER-USER -p tcp -m multiport --dports 80,443 -j DROP
sudo iptables -A DOCKER-USER -p udp --dport 53 -j DROP
sudo iptables -A DOCKER-USER -p tcp --dport 53 -j DROP

# Persist iptables rules across reboots
sudo netfilter-persistent save
sudo netfilter-persistent reload

# Enable UFW
sudo ufw --force enable
echo "Firewall configured."

# ── SSH hardening reminder ────────────────────────────────────────────────────
echo ""
echo "┌──────────────────────────────────────────────────────┐"
echo "│  SSH is reachable through the VPN only.              │"
echo "│  Recommended: disable SSH password authentication    │"
echo "│  (use key-based auth only):                          │"
echo "│                                                      │"
echo "│  On your laptop, copy your public key to the Pi:    │"
echo "│    ssh-copy-id pi@<pi-lan-ip>                        │"
echo "│                                                      │"
echo "│  Then on the Pi, disable passwords:                  │"
echo "│    sudo sed -i 's/^#\\?PasswordAuthentication.*/PasswordAuthentication no/' \\"
echo "│      /etc/ssh/sshd_config                            │"
echo "│    sudo systemctl restart ssh                        │"
echo "└──────────────────────────────────────────────────────┘"
echo ""

# ── Done ──────────────────────────────────────────────────────────────────────
PI_IP=$(hostname -I | awk '{print $1}')
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  Setup complete!                                             ║"
echo "╠══════════════════════════════════════════════════════════════╣"
echo "║                                                              ║"
echo "║  Access via WireGuard VPN:  https://10.8.0.1                ║"
echo "║  Access:                    https://10.8.0.1 (VPN only)   ║"
echo "║  Pi-hole Admin:             SSH tunnel to localhost:8080  ║"
echo "║                                                              ║"
echo "║  Next steps:                                                 ║"
echo "║  1. Download a model: python3 scripts/download_model.py      ║"
echo "║  2. Edit .env (POSTGRES_PASSWORD, LLM_MODEL_PATH, etc.)     ║"
echo "║  3. Deploy: bash scripts/pi_deploy.sh                       ║"
echo "║  4. Follow config/wireguard/setup.md — set up VPN clients   ║"
echo "║  5. On your router: ONLY forward UDP 51820 to $PI_IP        ║"
echo "║  6. (Optional) set VPN clients' DNS to 10.8.0.1             ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

if [[ "$DEPLOY_AFTER_SETUP" -eq 1 ]]; then
  bash "$PROJECT_DIR/scripts/pi_deploy.sh"
fi
