---
title: OSINTleaks Remote Search
emoji: 🔍
colorFrom: purple
colorTo: blue
sdk: docker
pinned: false
license: mit
---

# OSINTleaks Remote Search - Hugging Face Space

Bu Space, TeraBox dosyalarınızı uzaktan aramak için Alist + Rclone + Ripgrep + FastAPI kullanır.

## Özellikler

- **Alist**: TeraBox WebDAV bağlantısı
- **Rclone**: WebDAV'ı local mount eder
- **Ripgrep**: Hızlı metin arama
- **FastAPI**: REST API sunucusu

## Hugging Face Secrets

Space'e bu secrets'ları ekleyin:

| Secret | Açıklama | Örnek |
|--------|----------|-------|
| `ALIST_ADMIN_USER` | Alist admin kullanıcı adı | `admin` |
| `ALIST_ADMIN_PASSWORD` | Alist admin şifresi | `güçlü_şifre` |
| `ALIST_JWT_SECRET` | Alist JWT secret | `alist-jwt-secret` |
| `TERABOX_TOKEN` | TeraBox token | `terabox_token_123` |
| `RCLONE_CONFIG` | Rclone config (base64 encoded) | Veya boş bırakın |

## API Endpoint'leri

### Health Check
```
GET /
```
Response:
```json
{
  "status": "ok",
  "service": "OSINTleaks Remote Search",
  "mount_path": "/home/user/terabox_data",
  "mount_status": "mounted"
}
```

### Mount Status
```
GET /mount/status
```
Response:
```json
{
  "mounted": true,
  "path": "/home/user/terabox_data",
  "files_count": 1234,
  "size_mb": 1024.5
}
```

### Search
```
POST /search
```
Request:
```json
{
  "pattern": "discord",
  "skill": "discord",
  "case_insensitive": true,
  "max_results": 1000,
  "file_types": ["txt"]
}
```
Response:
```json
{
  "ok": true,
  "matches": [
    {
      "path": "/terabox/discord_tokens.txt",
      "line": 42,
      "content": "token: abc123...",
      "bytes_offset": 1024
    }
  ],
  "total": 42,
  "search_time": 0.5,
  "mount_status": "mounted"
}
```

## Mevcut OSINT Sitesi Entegrasyonu

Mevcut OSINT sitesine bu endpoint'i ekleyin:

```python
# app.py içinde
@app.route('/logs/remote_search', methods=['POST'])
@login_required
def remote_search():
    HF_SPACE_URL = "https://your-space.hf.space"
    response = requests.post(
        f"{HF_SPACE_URL}/search",
        json=request.json,
        timeout=300
    )
    return jsonify(response.json())
```

## Kullanım

1. Space'e secrets'ları ekleyin
2. Space'i build edin
3. `/mount/status` ile mount durumunu kontrol edin
4. `/search` ile arama yapın

## Notlar

- TeraBox WebDAV desteklemiyorsa, Alist panelinden manuel storage ekleyin
- Rclone config base64 encoded olarak `RCLONE_CONFIG` secret'ında verilebilir
- Mount her restart'ta tekrar yapılır
