#!/usr/bin/env bash
# Deploy the Investment Assistant on a Raspberry Pi 5.
# Assumes scripts/setup.sh has already installed Docker, generated TLS certs,
# and configured the firewall.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"
COMPOSE=(docker compose)

cd "$PROJECT_DIR"

die() {
  echo "ERROR: $*" >&2
  exit 1
}

read_env() {
  local key="$1"
  local raw
  raw="$(grep -E "^${key}=" "$ENV_FILE" | tail -n1 | cut -d= -f2- || true)"
  raw="${raw%%#*}"
  printf "%s" "$raw" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'
}

require_non_default_secret() {
  local key="$1"
  local value
  value="$(read_env "$key")"
  [[ -n "$value" ]] || die "$key is required in .env"
  [[ "$value" != change_me* ]] || die "$key still has the example value"
}

echo "Investment Assistant - Raspberry Pi deploy"
echo "Project: $PROJECT_DIR"
echo ""

[[ -f "$ENV_FILE" ]] || die ".env not found. Run: cp .env.example .env"
command -v docker >/dev/null 2>&1 || die "docker is not installed. Run scripts/setup.sh first."
docker compose version >/dev/null 2>&1 || die "Docker Compose plugin is not available."

require_non_default_secret "POSTGRES_PASSWORD"
require_non_default_secret "PIHOLE_PASSWORD"

model_path="$(read_env "LLM_MODEL_PATH")"
[[ -n "$model_path" ]] || die "LLM_MODEL_PATH is required."
[[ "$model_path" == /app/models/* ]] || die "LLM_MODEL_PATH must start with /app/models/"

host_model="$PROJECT_DIR/models/${model_path#/app/models/}"
[[ -f "$host_model" ]] || die "Model file not found on the Pi: $host_model"

mkdir -p "$PROJECT_DIR/config/nginx/certs" "$PROJECT_DIR/models"
cert="$PROJECT_DIR/config/nginx/certs/selfsigned.crt"
key="$PROJECT_DIR/config/nginx/certs/selfsigned.key"
if [[ ! -f "$cert" || ! -f "$key" ]]; then
  echo "Generating self-signed TLS certificate..."
  openssl req -x509 -nodes -days 3650 -newkey rsa:4096 \
    -keyout "$key" -out "$cert" \
    -subj "/C=PT/ST=Lisbon/L=Lisbon/O=InvestmentAssistant/CN=investment-assistant" \
    -quiet
  chmod 600 "$key"
fi

echo "Building app image..."
"${COMPOSE[@]}" build app

echo "Starting services..."
"${COMPOSE[@]}" up -d

echo "Waiting for app health..."
healthy=0
for _ in $(seq 1 60); do
  if "${COMPOSE[@]}" exec -T app python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)" \
    >/dev/null 2>&1; then
    healthy=1
    break
  fi
  sleep 2
done

"${COMPOSE[@]}" ps

if [[ "$healthy" -ne 1 ]]; then
  echo ""
  echo "App did not pass the local health check yet. Recent app logs:"
  "${COMPOSE[@]}" logs --tail=80 app
  exit 1
fi

pi_ip="$(hostname -I | awk '{print $1}')"
echo ""
echo "Deployment complete."
echo "LAN URL: https://$pi_ip"
echo "VPN URL: https://10.8.0.1"
echo "Logs:    docker compose logs -f"
