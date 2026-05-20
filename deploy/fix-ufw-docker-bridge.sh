#!/bin/bash
# Recovery: replace the broad UFW deny 8000/tcp (which blocks Docker
# bridge traffic from Caddy to host-bound uvicorn) with a narrower
# allow-from-Docker-subnets rule. Default-deny still drops external.
set -e

echo "[1/3] UFW: remove broad deny 8000/tcp, allow docker bridges"
ufw delete deny 8000/tcp 2>&1 || true
ufw allow from 172.16.0.0/12 to any port 8000 proto tcp comment 'docker bridges -> uvicorn' 2>&1 || true
ufw status | grep -E '(8000|172\.16)' || true

echo "[2/3] Sanity check from host"
curl -skI --max-time 5 http://127.0.0.1:8000/health | head -3 || true

echo "[3/3] End-to-end via Caddy"
echo "--- /health ---"
curl -skI --resolve obsyd.dev:443:127.0.0.1 --max-time 10 https://obsyd.dev/health | head -5 || true
echo "--- /api/prices/eia ---"
curl -skI --resolve obsyd.dev:443:127.0.0.1 --max-time 10 https://obsyd.dev/api/prices/eia | head -5 || true
echo "--- / ---"
curl -skI --resolve obsyd.dev:443:127.0.0.1 --max-time 10 https://obsyd.dev/ | head -5 || true
echo "--- external test ---"
curl -sI --max-time 10 https://obsyd.dev/health | head -5 || true
