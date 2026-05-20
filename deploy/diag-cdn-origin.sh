#!/bin/bash
# Diagnose how Hostinger CDN talks to this origin for valuekick.de.
# Tells us whether to keep TLS on the origin Caddy block, or drop it.
set -e

echo "=== [1] Last 200 Caddy log lines, filtered for valuekick.de ==="
docker logs valuekick-caddy-1 --tail 200 2>&1 | grep -iE 'valuekick\.de|"host":|"uri":' | tail -30 || echo "(no log entries found)"
echo
echo "=== [2] HTTP origin probe (mimics CDN -> origin via HTTP) ==="
curl -sI --max-time 5 -H "Host: valuekick.de" http://127.0.0.1/ | head -10 || echo "  HTTP origin not responding"
echo
echo "=== [3] HTTPS origin probe (mimics CDN -> origin via HTTPS) ==="
curl -skI --max-time 5 --resolve valuekick.de:443:127.0.0.1 https://valuekick.de/ | head -10 || echo "  HTTPS origin not responding"
echo
echo "=== [4] Public chain (via CDN) ==="
curl -sI --max-time 10 https://valuekick.de/ | head -8 || echo "  public chain not responding"
echo
echo "=== [5] Caddy current site blocks ==="
docker exec valuekick-caddy-1 caddy adapt --config /etc/caddy/Caddyfile 2>&1 | grep -oE '"host":\["[^"]+"\]' | sort -u || echo "  (caddy adapt failed)"
