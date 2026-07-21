#!/bin/bash
# Integrate obsyd.dev into the existing Caddy reverse-proxy used by ValueKick.
# Only touches: Caddyfile, docker-compose.prod.yml (Caddy service block),
# obsyd.service (uvicorn bind), UFW (deny 8000 from outside).
#
# Deploying the /embed exception (Task P10) to an ALREADY-deployed obsyd.dev:
# step [2/7] below is idempotent — it skips the whole obsyd.dev block (including
# this new @embed handle) whenever "obsyd.dev" already appears in the live
# Caddyfile, which it will on any server this script has already run on. So
# picking up the @embed block on a live host is a MANUAL step, not something
# re-running this script accomplishes:
#   1. On the VPS, open the live Caddyfile (the one this script appends to,
#      here /home/jo/valuekick/Caddyfile) and paste in the `@embed`
#      matcher + `handle @embed { ... }` block from step [2/7] below, placed
#      after `handle @api` and before the generic `handle { ... }` — matching
#      order matters (Caddy's `handle` blocks are mutually exclusive and
#      evaluated top-to-bottom, first match wins).
#   2. Apply it exactly like step [6/7] does: `docker compose -f
#      docker-compose.prod.yml up -d --force-recreate caddy` (a `caddy reload`
#      inside the container works too if you'd rather not recreate it).
# There is no live-server automation here on purpose — the script's own
# guard against double-appending would otherwise make it easy to believe a
# re-run had shipped this change when it silently no-opped.
set -e
TS=$(date +%s)
cd /home/jo/valuekick

echo "[1/7] Backups"
cp docker-compose.prod.yml docker-compose.prod.yml.bak.$TS
cp Caddyfile Caddyfile.bak.$TS
cp /etc/systemd/system/obsyd.service /etc/systemd/system/obsyd.service.bak.$TS
echo "  -> *.bak.$TS"

echo "[2/7] Append obsyd.dev block to Caddyfile"
if grep -q "obsyd.dev" Caddyfile; then
  echo "  already present, skip"
else
cat >> Caddyfile <<'CADDY_EOF'

obsyd.dev, www.obsyd.dev {
    encode gzip zstd

    header {
        X-Content-Type-Options "nosniff"
        Referrer-Policy "strict-origin-when-cross-origin"
        X-Frame-Options "DENY"
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
    }

    @api path /api/* /health
    handle @api {
        reverse_proxy host.docker.internal:8000 {
            header_up X-Real-IP {remote_host}
            header_up X-Forwarded-For {remote_host}
        }
    }

    # Task P10 (embeddable zone widgets): /embed/* is the ONLY frameable part of the
    # site. The global X-Frame-Options DENY above blocks ALL embedding by default —
    # here we punch a narrow, path-scoped hole for the iframe widgets only, and only
    # for that path. Must come AFTER @api (so /api/* keeps its own headers/proxy) and
    # BEFORE the generic catch-all `handle` below (or its unscoped DENY wins first).
    @embed path /embed/*
    handle @embed {
        header -X-Frame-Options
        header Content-Security-Policy "frame-ancestors *"
        header Cache-Control "public, max-age=300"
        root * /srv/obsyd
        try_files {path} /index.html
        file_server
    }

    handle {
        root * /srv/obsyd
        try_files {path} /index.html
        file_server
    }

    log {
        output stdout
        format console
    }
}
CADDY_EOF
  echo "  appended"
fi

echo "[3/7] Patch docker-compose.prod.yml (extra_hosts + obsyd mount)"
python3 <<'PY'
from pathlib import Path
p = Path("/home/jo/valuekick/docker-compose.prod.yml")
t = p.read_text()
orig = t
if "host.docker.internal:host-gateway" not in t:
    t = t.replace(
        "  caddy:\n    image: caddy:2-alpine\n    restart: unless-stopped\n",
        "  caddy:\n    image: caddy:2-alpine\n    restart: unless-stopped\n"
        "    extra_hosts:\n      - \"host.docker.internal:host-gateway\"\n",
    )
if "/srv/obsyd:ro" not in t:
    t = t.replace(
        "      - ./Caddyfile:/etc/caddy/Caddyfile:ro\n",
        "      - ./Caddyfile:/etc/caddy/Caddyfile:ro\n"
        "      - /home/obsyd/obsyd/frontend/dist:/srv/obsyd:ro\n",
    )
if t != orig:
    p.write_text(t)
    print("  compose patched")
else:
    print("  compose already patched")
PY

echo "[4/7] uvicorn bind 0.0.0.0 + restart"
sed -i 's/--host 127.0.0.1 --port 8000/--host 0.0.0.0 --port 8000/' /etc/systemd/system/obsyd.service
systemctl daemon-reload
systemctl restart obsyd
sleep 3
echo "  obsyd: $(systemctl is-active obsyd)"

echo "[5/7] UFW deny 8000/tcp from outside"
ufw deny 8000/tcp comment 'uvicorn host-internal only' 2>&1 || true

echo "[6/7] Recreate Caddy"
docker compose -f docker-compose.prod.yml up -d --force-recreate caddy 2>&1 | tail -5
sleep 8

echo "[7/7] Verification"
echo "--- listen sockets ---"
ss -tlnp | grep -E ':(80|443|8000)\b' || echo "  none"
echo "--- caddy container ---"
docker ps --filter name=caddy --format '{{.Names}}: {{.Status}}'
echo "--- caddy logs (last 25) ---"
CADDY=$(docker ps -qf name=caddy | head -1)
docker logs --tail 25 "$CADDY" 2>&1
echo "--- curl /health (local, Host: obsyd.dev) ---"
curl -skI --resolve obsyd.dev:443:127.0.0.1 --max-time 10 https://obsyd.dev/health | head -5 || true
echo "--- curl / ---"
curl -skI --resolve obsyd.dev:443:127.0.0.1 --max-time 10 https://obsyd.dev/ | head -5 || true
echo "--- valuekick.de sanity ---"
curl -skI --resolve valuekick.de:443:127.0.0.1 --max-time 10 https://valuekick.de/ | head -3 || true
