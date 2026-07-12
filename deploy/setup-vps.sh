#!/usr/bin/env bash
# OBSYD VPS Setup Script — STANDALONE self-host path (single-tenant VPS).
# Provisions a dedicated nginx + Let's Encrypt (certbot) reverse proxy in front of
# the systemd service. This is a valid, self-contained way to self-host OBSYD.
#
# NOTE: the hosted deployment at obsyd.dev does NOT use this script — it runs
# behind Caddy shared with another app (see deploy/install-caddy-integration.sh).
# If you already run Caddy, skip the nginx steps here and use that integration
# instead. Either reverse proxy works; pick one.
#
# Run as root on a fresh Ubuntu/Debian VPS
#
# Usage: ssh root@<VPS_IP> 'bash -s' < deploy/setup-vps.sh
#    OR: scp deploy/setup-vps.sh root@<VPS_IP>: && ssh root@<VPS_IP> bash setup-vps.sh

set -euo pipefail

echo "=== OBSYD VPS Setup ==="

# 1. System packages
echo "[1/6] Installing system packages..."
apt update -qq
apt install -y -qq python3 python3-pip python3-venv nginx certbot python3-certbot-nginx git curl sqlite3 htop ufw

# 2. Create obsyd user (skip if exists)
if ! id obsyd &>/dev/null; then
    echo "[2/6] Creating obsyd user..."
    adduser --disabled-password --gecos "OBSYD" obsyd
    usermod -aG sudo obsyd
else
    echo "[2/6] obsyd user already exists, skipping"
fi

# 3. Firewall
echo "[3/6] Configuring firewall..."
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable
echo "Firewall: $(ufw status | grep Status)"

# 4. Install systemd service
echo "[4/6] Installing systemd service..."
cp /home/obsyd/obsyd/deploy/obsyd.service /etc/systemd/system/obsyd.service
systemctl daemon-reload
systemctl enable obsyd
echo "systemd service installed (not started yet — need .env + venv first)"

# 5. Install nginx config
echo "[5/6] Configuring nginx..."
cp /home/obsyd/obsyd/deploy/obsyd.nginx /etc/nginx/sites-available/obsyd
ln -sf /etc/nginx/sites-available/obsyd /etc/nginx/sites-enabled/obsyd
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx
echo "nginx configured and reloaded"

# 6. Health-check cron (+ optional off-box liveness ping)
# On success, ping HEALTHCHECKS_URL if set (an off-box dead-man's-switch so an
# external monitor notices when the whole VPS/cron dies — the on-box restart
# below can't). Set the URL by editing the crontab env line after creating a
# check at healthchecks.io / UptimeRobot; empty = no-op.
echo "[6/7] Installing health-check cron..."
# Dedupe filter matches ONLY this health line ('grep -v obsyd' would also wipe
# the docker-prune line installed in step 9 — its path contains "obsyd"), and a
# HEALTHCHECKS_URL the operator already configured survives a re-run.
# NB: an EMPTY env value must be written as NAME="" — vixie cron rejects a bare
# NAME= line ("bad minute") and then refuses the ENTIRE crontab (hit live 2026-07-12).
(crontab -l 2>/dev/null | grep -v 'systemctl restart obsyd' | grep -v '^HEALTHCHECKS_URL='; \
 crontab -l 2>/dev/null | grep '^HEALTHCHECKS_URL=' || echo 'HEALTHCHECKS_URL=""'; \
 echo '*/5 * * * * if curl -sf http://127.0.0.1:8000/health >/dev/null; then [ -n "$HEALTHCHECKS_URL" ] && curl -fsS -m 10 --retry 2 "$HEALTHCHECKS_URL" >/dev/null 2>&1; else systemctl restart obsyd; fi') | crontab -
echo "Cron health-check installed (set HEALTHCHECKS_URL in root crontab to enable off-box ping)"

# 7. Daily DB backup cron (as obsyd user; offsite target via OBSYD_BACKUP_REMOTE)
# backup-db.sh rsyncs offsite only when OBSYD_BACKUP_REMOTE is set (else backups
# stay on the same disk as the live DB — a disk loss then loses both). Fill in the
# crontab env line with e.g. "user@host:/srv/obsyd-backups/" after adding an SSH key.
echo "[7/7] Installing daily backup cron..."
chmod +x /home/obsyd/obsyd/deploy/backup-db.sh
sudo -u obsyd bash -c '(crontab -l 2>/dev/null | grep -v backup-db.sh | grep -v "^OBSYD_BACKUP_REMOTE="; \
  crontab -l 2>/dev/null | grep "^OBSYD_BACKUP_REMOTE=" || echo "OBSYD_BACKUP_REMOTE=\"\""; \
  echo "30 3 * * * /home/obsyd/obsyd/deploy/backup-db.sh >> /home/obsyd/backups/backup.log 2>&1") | crontab -'
echo "Daily backup cron installed (03:30, retention 14 days; set OBSYD_BACKUP_REMOTE for offsite)"

# 8. Disk alarm cron (as obsyd user, every 15 min — the 2026-07-07 disk-full
# incident took dockerd, Caddy and both sites down for two days before anyone
# noticed; this is the script written as its post-mortem, so it MUST be wired).
echo "[8/9] Installing disk-alarm cron..."
chmod +x /home/obsyd/obsyd/deploy/disk-alarm.sh
sudo -u obsyd bash -c 'mkdir -p /home/obsyd/obsyd/logs; (crontab -l 2>/dev/null | grep -v disk-alarm.sh; \
  echo "*/15 * * * * /home/obsyd/obsyd/deploy/disk-alarm.sh >> /home/obsyd/obsyd/logs/disk-alarm.log 2>&1") | crontab -'
echo "Disk-alarm cron installed (*/15 min, obsyd crontab)"

# 9. Weekly docker prune (root — needs docker rights). Dangling images + build
# cache only; never volumes (valuekick postgres) and never `image prune -a`
# (would reap node:20 that valuekick's 30-min cron re-pulls). See script header.
echo "[9/9] Installing docker-prune cron..."
chmod +x /home/obsyd/obsyd/deploy/docker-prune.sh
(crontab -l 2>/dev/null | grep -v docker-prune.sh; \
 echo '15 4 * * 0 /home/obsyd/obsyd/deploy/docker-prune.sh >> /home/obsyd/obsyd/logs/docker-prune.log 2>&1') | crontab -
echo "Docker-prune cron installed (Sun 04:15, root crontab)"

echo ""
echo "=== VPS base setup complete ==="
echo ""
echo "Next steps (as obsyd user):"
echo "  1. cd ~/obsyd && python3 -m venv .venv"
echo "  2. source .venv/bin/activate && pip install -r requirements.txt"
echo "  3. Copy .env to ~/obsyd/.env and chmod 600 .env"
echo "  4. sudo systemctl start obsyd"
echo "  5. curl http://127.0.0.1:8000/health"
