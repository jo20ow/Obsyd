#!/bin/bash
# Recovery: ensure DOMAIN env exists for the shared Caddy on this VPS,
# then recreate Caddy. Run after install-caddy-integration.sh if Caddy
# crashes with "unrecognized global option: encode" (which means the
# `{$DOMAIN:...}` placeholder in the ValueKick Caddyfile substituted
# to an empty string at parse time, turning the site block into a
# malformed global-options block).
set -e
cd /home/jo/valuekick

echo "[1/4] Current env files in valuekick dir"
ls -la .env* 2>&1 || echo "  (none)"

echo "[2/4] Ensure DOMAIN=valuekick.de is set in .env"
if grep -q '^DOMAIN=' .env 2>/dev/null; then
  echo "  DOMAIN already set: $(grep '^DOMAIN=' .env)"
else
  echo "DOMAIN=valuekick.de" >> .env
  echo "  appended DOMAIN=valuekick.de"
fi
chmod 600 .env

echo "[3/4] Recreate caddy with fresh env"
docker compose -f docker-compose.prod.yml up -d --force-recreate caddy 2>&1 | tail -5
sleep 6

echo "[4/4] Verification"
echo "--- caddy container ---"
docker ps --filter name=caddy --format '{{.Names}}: {{.Status}}'
echo "--- caddy logs (last 15) ---"
docker logs --tail 15 valuekick-caddy-1 2>&1
echo "--- listen sockets ---"
ss -tlnp | grep -E ':(80|443|8000)\b' || echo "  none"
echo "--- /health (Host: obsyd.dev, local) ---"
curl -skI --resolve obsyd.dev:443:127.0.0.1 --max-time 10 https://obsyd.dev/health | head -5 || true
echo "--- / (Host: obsyd.dev, local) ---"
curl -skI --resolve obsyd.dev:443:127.0.0.1 --max-time 10 https://obsyd.dev/ | head -5 || true
echo "--- valuekick.de sanity ---"
curl -skI --resolve valuekick.de:443:127.0.0.1 --max-time 10 https://valuekick.de/ | head -3 || true
