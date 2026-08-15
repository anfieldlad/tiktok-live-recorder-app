# SSH — Server & Infrastructure Reference

Guidance for AI agents that need to understand, debug, or modify the production server for this project.

Sensitive connection details (IP, user, key path) are stored in `.ssh-config.local` on the local machine — gitignored, never committed.

---

## Connecting

See `.ssh-config.local` for the actual host, user, and key path. It looks like:

```env
SSH_HOST=<server-ip>
SSH_USER=<user>
SSH_KEY=~/.ssh/<keyfile>
```

SSH command:

```bash
ssh -i $SSH_KEY $SSH_USER@$SSH_HOST
```

For AI agents running non-interactively from this Windows machine via WSL:

```bash
wsl ssh -i /mnt/c/Users/<username>/.ssh/<keyfile> -o StrictHostKeyChecking=no $SSH_USER@$SSH_HOST 'your command here'
```

> Password authentication is **disabled** on the server. Key auth is the only way in.

---

## App Service (systemd)

The app runs as a systemd service named `ttl-downloader`.

**Service file:** `/etc/systemd/system/ttl-downloader.service`

```ini
[Unit]
Description=TTL Downloader FastAPI
After=network.target

[Service]
User=<app-user>
Group=<app-user>
WorkingDirectory=/opt/ttl-downloader
EnvironmentFile=/opt/ttl-downloader/.env
ExecStart=/opt/ttl-downloader/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

### Common service commands

```bash
# Start / stop / restart
sudo systemctl start ttl-downloader
sudo systemctl stop ttl-downloader
sudo systemctl restart ttl-downloader

# Check status
sudo systemctl status ttl-downloader

# Watch live logs
journalctl -u ttl-downloader -f

# Show last N lines
journalctl -u ttl-downloader -n 100 --no-pager
```

### After a `git pull`

Always install dependencies before restarting — new packages will not be picked up automatically:

```bash
cd /opt/ttl-downloader
/opt/ttl-downloader/.venv/bin/pip install -r requirements.txt
sudo systemctl restart ttl-downloader
```

> **Important:** The server uses a virtualenv at `/opt/ttl-downloader/.venv`.  
> Never use the system `python3` or `pip3` directly — they are not the same environment.  
> Running `pip install` with the system Python will fail with `externally-managed-environment`.

---

## Python Environment

| Item | Path |
|---|---|
| Virtualenv | `/opt/ttl-downloader/.venv` |
| Python binary | `/opt/ttl-downloader/.venv/bin/python` |
| pip | `/opt/ttl-downloader/.venv/bin/pip` |
| uvicorn | `/opt/ttl-downloader/.venv/bin/uvicorn` |

```bash
# Install / update dependencies
/opt/ttl-downloader/.venv/bin/pip install -r /opt/ttl-downloader/requirements.txt

# Run a one-off Python command in the correct environment
cd /opt/ttl-downloader && .venv/bin/python3 -c "..."
```

---

## Environment Variables

**File:** `/opt/ttl-downloader/.env` (loaded by systemd via `EnvironmentFile=`, gitignored — never commit)

Key variables:

| Variable | Description |
|---|---|
| `APP_ENV` | `production` |
| `HOST` / `PORT` | uvicorn bind address |
| `JOBS_FILE` | Path to recording job store JSON |
| `OUTPUT_DIR` | Where downloaded media is saved |
| `LOGS_DIR` | Recorder log files |
| `RECORDER_DIR` | Path to upstream tiktok-live-recorder clone |
| `PYTHON_BIN` | Python binary inside recorder's own venv |
| `ROOT_PATH` | FastAPI subpath prefix — currently `/stillhere`; affects routing |

`ROOT_PATH` tells FastAPI the subpath it is mounted under. This affects routing — see the nginx section below. It is the **canonical** prefix: every link, redirect and asset URL the app generates is built from it, so it must match the canonical `location` block exactly.

---

## Nginx

**Config file:** `/etc/nginx/sites-enabled/`

The app answers on **two** prefixes. `/stillhere/` is canonical; `/tiktok/` is a
compatibility shim for the Android client — see [Retiring `/tiktok`](#retiring-tiktok).

```nginx
server {
    server_name <domain>;

    # nginx listens on 8443 behind sslh, so absolute redirects were built as
    # https://<domain>:8443/... — a port ufw blocks, which broke every bare
    # URL without a trailing slash. Emit relative Location headers instead.
    absolute_redirect off;

    # --- canonical mount: matches ROOT_PATH=/stillhere ---
    location = /stillhere {
        return 301 /stillhere/;
    }

    location /stillhere/ {
        proxy_pass http://127.0.0.1:8000;   # no trailing slash — passes full URI to uvicorn
        proxy_http_version 1.1;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # --- compatibility shim: the Android client ships /tiktok/ base URLs ---
    location = /tiktok {
        return 301 /tiktok/;
    }

    location /tiktok/ {
        proxy_pass http://127.0.0.1:8000/stillhere/;   # WITH a path — swaps the prefix upstream
        proxy_http_version 1.1;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    listen 443 ssl;
    # SSL managed by Certbot
}
```

### Critical: the canonical block's proxy_pass must NOT have a trailing slash

`proxy_pass http://127.0.0.1:8000;` — **no trailing slash**, for `/stillhere/`.

- **With** a trailing slash (`proxy_pass http://127.0.0.1:8000/;`): nginx strips `/stillhere/` before forwarding. Uvicorn receives `/static/css/app.css` → 404, because FastAPI (Starlette 0.47+) routes on the full path including the `ROOT_PATH` prefix.
- **Without** a trailing slash: nginx forwards the full URI `/stillhere/static/css/app.css`. FastAPI receives it, matches `ROOT_PATH=/stillhere`, and serves correctly.

The `/tiktok/` shim inverts this deliberately. `proxy_pass http://127.0.0.1:8000/stillhere/;`
carries a path, so nginx **replaces** the matched `/tiktok/` prefix with `/stillhere/`
before forwarding. The app therefore only ever sees one prefix and needs no
multi-mount awareness. Payloads are unaffected: `file_urls` are returned without
any root prefix (`/downloads/{id}/files/0`), and each client prepends its own base.

### Retiring `/tiktok`

The shim exists only because the Android client hardcodes `/tiktok/` base URLs and
cannot be repointed without a release. Once a rebuilt client shipping `/stillhere/`
is rolled out and old installs are drained, delete both `/tiktok` blocks above and
reload nginx. Nothing else references the prefix — the app, its links and its
payloads are already fully on `/stillhere`.

### Retiring the synchronous download door

`POST /downloads` and `POST /instagram/downloads` without `?async=1` submit a job
and hold the connection open until it finishes. That exists only because the
shipped Android build (`com.ttldownloader.app`, versionCode 5) posts and waits.

**Delete both synchronous branches once Still Here mobile ships against the
async door** — same trigger as the `/tiktok` prefix above, and worth doing in the
same change.

Until then, `/stillhere/` and `/tiktok/` need `proxy_read_timeout` raised: nginx
defaults to 60 seconds, so any download slower than that already 504s and reads
to the user as a download failure. The pattern is in the `/breaking-bad/` block
on the same box, which sets `proxy_read_timeout 86400`.

### Common nginx commands

```bash
# Test config syntax
sudo nginx -t

# Reload config (no downtime)
sudo systemctl reload nginx

# Restart nginx
sudo systemctl restart nginx

# Watch nginx access log
tail -f /var/log/nginx/access.log

# Watch nginx error log
tail -f /var/log/nginx/error.log
```

---

## Deploy Checklist

When pulling new code to the server:

```bash
cd /opt/ttl-downloader

# 1. Pull latest code
git pull

# 2. Install any new Python dependencies
/opt/ttl-downloader/.venv/bin/pip install -r requirements.txt

# 3. Restart the app
sudo systemctl restart ttl-downloader

# 4. Confirm it started cleanly
sudo systemctl status ttl-downloader
journalctl -u ttl-downloader -n 20 --no-pager
```

If nginx config was changed:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

---

## Directory Layout on Server

```
/opt/ttl-downloader/
├── .venv/                  # Python virtualenv (do not commit)
├── .env                    # Environment variables (do not commit)
├── app/                    # Application source
│   ├── api/
│   ├── models/
│   ├── services/
│   ├── static/
│   │   ├── css/app.css
│   │   └── js/
│   └── templates/
├── data/                   # Runtime job store JSON files
├── logs/                   # Recorder logs
├── output/                 # Downloaded media
├── vendor/
│   └── tiktok-live-recorder/   # Upstream recorder (separate venv)
│       └── .venv/
└── requirements.txt
```

---

## Troubleshooting

### `ModuleNotFoundError` on restart
New dependency added to `requirements.txt` but not installed. Run:
```bash
/opt/ttl-downloader/.venv/bin/pip install -r requirements.txt
sudo systemctl restart ttl-downloader
```

### Static files return 404
Check that `proxy_pass` in nginx does **not** have a trailing slash. See the nginx section above.

### `externally-managed-environment` error from pip
You are using the system Python instead of the virtualenv. Use:
```bash
/opt/ttl-downloader/.venv/bin/pip install ...
```

### App starts but pages are blank / JS errors
Check browser console for failed static file requests. If static files 404, check nginx `proxy_pass` config and reload nginx.

### Check what port uvicorn is listening on
```bash
ss -tlnp | grep 8000
```
