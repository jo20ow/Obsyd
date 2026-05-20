#!/bin/bash
# Stop the Let's Encrypt renewal loop for valuekick.de on the origin
# Caddy. Hostinger CDN sits in front (DNS A-record points to CDN, not
# this VPS), so the HTTP-01 challenge never reaches the origin and
# every renewal attempt fails. The CDN does its own public TLS — the
# origin only needs *any* TLS endpoint for the CDN backhaul, which
# `tls internal` provides via a self-signed cert.
#
# Safety: validates Caddyfile + reloads. If the public valuekick.de
# breaks after the change (Hostinger CDN rejecting the self-signed
# origin cert), this script rolls back automatically.
set -e
cd /home/jo/valuekick
TS=$(date +%s)

echo "[1/5] Backup Caddyfile"
cp Caddyfile Caddyfile.bak.$TS
echo "  -> Caddyfile.bak.$TS"

echo "[2/5] Inject 'tls internal' into the valuekick site block"
python3 <<'PY'
import re, sys
from pathlib import Path
p = Path("/home/jo/valuekick/Caddyfile")
t = p.read_text()
if "tls internal" in t:
    print("  already present, skip")
    sys.exit(0)
new = re.sub(
    r'(\{\$DOMAIN:valuekick\.example\.com\}\s*\{\n)',
    r'\1    tls internal\n',
    t, count=1
)
if new == t:
    print("  ERROR: anchor '{$DOMAIN:valuekick.example.com} {' not found")
    sys.exit(1)
p.write_text(new)
print("  injected")
PY

echo "[3/5] Validate + reload Caddy"
if docker exec valuekick-caddy-1 caddy validate --config /etc/caddy/Caddyfile 2>&1 | tail -5 \
   && docker exec valuekick-caddy-1 caddy reload --config /etc/caddy/Caddyfile 2>&1 | tail -5; then
  echo "  reload OK"
else
  echo "  validation or reload failed -> rollback"
  cp Caddyfile.bak.$TS Caddyfile
  docker exec valuekick-caddy-1 caddy reload --config /etc/caddy/Caddyfile 2>&1 | tail -5 || true
  exit 1
fi
sleep 4

echo "[4/5] Verify external services"
VK=$(curl -sI --max-time 10 https://valuekick.de/ | head -1 | awk '{print $2}')
OB_STATIC=$(curl -sI --max-time 10 --resolve obsyd.dev:443:127.0.0.1 https://obsyd.dev/ | head -1 | awk '{print $2}')
OB_API=$(curl -sI --max-time 10 --resolve obsyd.dev:443:127.0.0.1 https://obsyd.dev/health | head -1 | awk '{print $2}')
echo "  valuekick.de (extern via CDN): HTTP $VK"
echo "  obsyd.dev / (intern, frontend): HTTP $OB_STATIC"
echo "  obsyd.dev /health (intern, api): HTTP $OB_API (405 = HEAD-on-GET, normal)"

if [ "$VK" != "200" ] && [ "$VK" != "301" ] && [ "$VK" != "302" ]; then
  echo "  valuekick.de NOT OK ($VK) -> ROLLBACK"
  cp Caddyfile.bak.$TS Caddyfile
  docker exec valuekick-caddy-1 caddy reload --config /etc/caddy/Caddyfile 2>&1 | tail -5 || true
  echo "  rolled back; Caddyfile restored from .bak.$TS"
  exit 1
fi

echo "[5/5] Confirm no more LE errors for valuekick.de"
sleep 5
ERR_COUNT=$(docker logs valuekick-caddy-1 --since 10s 2>&1 | grep -ciE 'valuekick\.de.*(obtain|challenge|unauthorized)' || true)
echo "  LE-error log lines in last 10s: $ERR_COUNT (target: 0)"
echo
echo "DONE. valuekick.de routes via CDN -> origin (now self-signed for backhaul); obsyd.dev unchanged."
