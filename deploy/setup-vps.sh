#!/usr/bin/env bash
# OBSYD VPS Setup Script
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

# 6. Health-check cron
echo "[6/6] Installing health-check cron..."
(crontab -l 2>/dev/null | grep -v obsyd; echo "*/5 * * * * curl -sf http://127.0.0.1:8000/health > /dev/null || systemctl restart obsyd") | crontab -
echo "Cron health-check installed"

echo ""
echo "=== VPS base setup complete ==="
echo ""
echo "Next steps (as obsyd user):"
echo "  1. cd ~/obsyd && python3 -m venv .venv"
echo "  2. source .venv/bin/activate && pip install -r requirements.txt"
echo "  3. Copy .env to ~/obsyd/.env and chmod 600 .env"
echo "  4. sudo systemctl start obsyd"
echo "  5. curl http://127.0.0.1:8000/health"
