#!/bin/bash

echo "=== OSINTleaks Başlatılıyor ==="

# Set Alist site URL for proxy - use empty string to let Alist handle routing
export ALIST_SITE_URL=""

# 1. Alist başlat
echo "[1/3] Alist başlatılıyor..."
# Alist config oluştur
mkdir -p /root/.config/alist
cat > /root/.config/alist/config.json <<EOF
{
  "force": false,
  "site_url": "",
  "cdn": "",
  "jwt_secret": "${ALIST_JWT_SECRET:-alist-jwt-secret}",
  "token_expires_in": 48,
  "database": {
    "type": "sqlite3",
    "host": "",
    "port": 0,
    "user": "",
    "password": "",
    "name": "data/data.db",
    "db_file": "data/data.db",
    "table_prefix": "x_",
    "ssl_mode": "",
    "DSN": ""
  },
  "scheme": {
    "address": "0.0.0.0",
    "http_port": 5244,
    "https_port": -1,
    "force_https": false,
    "cert_file": "",
    "key_file": "",
    "tls_cert_file": "",
    "tls_key_file": "",
    "tls_insecure_skip_verify": true,
    "unix_socket": "",
    "unix_socket_perm": "",
    "proxy": "",
    "delay": 0
  }
}
EOF

# Alist'i arka planda başlat
alist server &
ALIST_PID=$!
echo "Alist başlatıldı (PID: $ALIST_PID)"

# Alist'in hazır olmasını bekle (max 30 saniye)
echo "Alist'in başlaması bekleniyor..."
for i in {1..30}; do
    if curl -s http://localhost:5244/ping > /dev/null 2>&1; then
        echo "Alist hazır!"
        break
    fi
    echo "Bekleniyor... ($i/30)"
    sleep 1
done

# 2. Rclone config oluştur ve mount et
echo "[2/3] Rclone mount başlatılıyor..."
mkdir -p /root/.config/rclone

if [ -n "$RCLONE_CONFIG" ]; then
    # Environment variable'dan rclone config'i al
    echo "$RCLONE_CONFIG" | base64 -d > /root/.config/rclone/rclone.conf
else
    # Varsayılan config (Alist WebDAV)
    cat > /root/.config/rclone/rclone.conf <<EOF
[alist-webdav]
type = webdav
url = http://localhost:5244/dav
vendor = other
user = ${ALIST_ADMIN_USER:-admin}
pass = ${ALIST_ADMIN_PASSWORD:-password}
EOF
fi

# Mount point oluştur
mkdir -p /home/user/terabox_data

# Rclone mount (background)
rclone mount alist-webdav:/terabox /home/user/terabox_data \
    --vfs-cache-mode full \
    --vfs-cache-max-size 10G \
    --allow-other \
    --no-modtime \
    --daemon

echo "Rclone mount tamamlandı"

# Mount'un başarılı olduğunu kontrol et
sleep 2
if mountpoint -q /home/user/terabox_data; then
    echo "Mount başarılı: /home/user/terabox_data"
    ls -la /home/user/terabox_data | head -20
else
    echo "UYARI: Mount başarısız olabilir"
fi

# 3. Flask başlat
echo "[3/3] Flask başlatılıyor (port 5000)..."
cd /app
python app.py
