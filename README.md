# TikTok Live Recorder App

This project is a local app for recording TikTok Live streams.

It includes:
- a browser UI with separate `Record Now` and `Watch Mode` pages
- a FastAPI backend
- local job tracking, watch tracking, and downloads
- lightweight diagnostics for health and runtime status

UI and backend powered by [`Michele0303/tiktok-live-recorder`](https://github.com/Michele0303/tiktok-live-recorder).

## What It Does

- record a TikTok Live by username or live URL
- watch an account and auto-start recording when the live begins
- show clear recording status in the browser
- allow only one active recording at a time
- stop a running recording
- download the finished file
- delete the file automatically after download
- support a guided TikTok session flow for private or restricted lives
- expose runtime diagnostics for troubleshooting

## Architecture

This app is split into two layers:

- UI and backend in this repository
- recording engine from `Michele0303/tiktok-live-recorder`

This repository is the application layer.
The Michele0303 project is the recorder engine that handles TikTok live access and stream capture.

## Project Structure

```text
app/
  api/
  models/
  services/
  templates/
data/
logs/
output/
vendor/
```

## Prerequisites

For local development:

- Python 3.11+
- FFmpeg
- Git

Optional but recommended:

- `uv` for setting up the upstream recorder environment

## Local Setup

### 1. Clone this project

```powershell
git clone https://github.com/anfieldlad/tiktok-live-recorder-app.git ttl-downloader
cd ttl-downloader
```

### 2. Clone the recorder engine

```powershell
git clone https://github.com/Michele0303/tiktok-live-recorder.git vendor/tiktok-live-recorder
```

### 3. Set up the upstream recorder

```powershell
cd vendor\tiktok-live-recorder
uv venv
uv sync
.\.venv\Scripts\python.exe src\main.py -h
cd ..\..
```

### 4. Set up this app

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

### 5. Install FFmpeg

On Windows:

```powershell
choco install ffmpeg -y
```

Verify:

```powershell
ffmpeg -version
```

### 6. Run locally

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open:

- [http://127.0.0.1:8000](http://127.0.0.1:8000)

## Local Configuration

Important values in `.env`:

- `RECORDER_DIR`
- `RECORDER_ENTRYPOINT`
- `PYTHON_BIN`
- `OUTPUT_DIR`
- `JOBS_FILE`

By default, this project expects the upstream recorder at:

```text
vendor/tiktok-live-recorder
```

## Local Usage

### Record a live

1. Enter a TikTok username or live URL.
2. Click `Record`.
3. If the account is live and accessible, a job will be created.
4. If the account is offline, invalid, or private, the UI will show a clear message.
5. If the account is offline, the UI can send you directly to `Watch Mode`.

### Watch for a live

1. Open the `Watch Mode` page.
2. Enter a TikTok username or live URL.
3. Click `Watch and auto-record`.
4. The app will keep checking the account.
5. When the live becomes available, recording will start automatically.

### Sign in for private or restricted lives

If a live requires authentication:

1. Open the UI.
2. Use the guided `Unlock private lives` flow.
3. Open a real Chrome or Edge login window.
4. Sign in to TikTok in that browser.
5. Close that login window.
6. Capture the session.
7. Try recording again.

### Download a finished file

After a recording is finished:

1. Click `Download`.
2. The file will be streamed to the browser.
3. After the download completes, the file and its job metadata will be removed automatically.

## API Overview

### Start recording

```http
POST /recordings
Content-Type: application/json

{
  "username": "example_user"
}
```

Or:

```json
{
  "url": "https://www.tiktok.com/@example/live"
}
```

Optional:

```json
{
  "username": "example_user",
  "duration": 120
}
```

### List jobs

```http
GET /recordings
```

### Health checks

```http
GET /health
GET /health/details
```

`/health` returns a simple `{"status":"ok"}` response.

`/health/details` returns a richer diagnostics payload including:

- app environment and root path
- cookie and browser-login state
- recording and watch counts
- recorder/watch service diagnostics
- store recovery metadata

### Create a watch

```http
POST /watch-recordings
Content-Type: application/json

{
  "username": "example_user"
}
```

Or:

```json
{
  "url": "https://www.tiktok.com/@example/live"
}
```

### List watches

```http
GET /watch-recordings
```

### Stop a watch

```http
POST /watch-recordings/{watch_id}/stop
```

### Delete a watch

```http
DELETE /watch-recordings/{watch_id}
```

### Get one job

```http
GET /recordings/{job_id}
```

### Stop a running job

```http
POST /recordings/{job_id}/stop
```

### Download output

```http
GET /recordings/{job_id}/download
```

### Delete a job

```http
DELETE /recordings/{job_id}
```

## Deploying to an Ubuntu VPS

This is the recommended production shape for a small VPS.

### 1. Install system packages

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip ffmpeg git nginx
```

### 2. Create an app directory

```bash
sudo mkdir -p /opt/ttl-downloader
sudo chown $USER:$USER /opt/ttl-downloader
cd /opt/ttl-downloader
```

### 3. Clone this project

```bash
git clone https://github.com/anfieldlad/tiktok-live-recorder-app.git .
```

### 4. Clone the upstream recorder

```bash
git clone https://github.com/Michele0303/tiktok-live-recorder.git vendor/tiktok-live-recorder
```

### 5. Set up the upstream recorder environment

```bash
cd vendor/tiktok-live-recorder
python3 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -e .
python src/main.py -h
cd /opt/ttl-downloader
```

### 6. Set up the app environment

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
cp .env.example .env
```

### 7. Update `.env`

Example values:

```env
APP_ENV=production
HOST=127.0.0.1
PORT=8000
JOBS_FILE=/opt/ttl-downloader/data/jobs.json
OUTPUT_DIR=/opt/ttl-downloader/output
LOGS_DIR=/opt/ttl-downloader/logs
RECORDER_DIR=/opt/ttl-downloader/vendor/tiktok-live-recorder
RECORDER_ENTRYPOINT=/opt/ttl-downloader/vendor/tiktok-live-recorder/src/main.py
PYTHON_BIN=/opt/ttl-downloader/vendor/tiktok-live-recorder/.venv/bin/python
RECORDER_MODE=manual
SKIP_UPDATE_CHECK=true
CLEANUP_MAX_AGE_HOURS=3
```

### 8. Test the app manually

```bash
. .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open:

- `http://your-server-ip:8000`

Stop it after the test and continue with `systemd`.

You can verify the app and favicon routes with:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/health/details
curl http://127.0.0.1:8000/favicon.svg
```

### 9. Create a systemd service

Create:

```bash
sudo nano /etc/systemd/system/ttl-downloader.service
```

Use:

```ini
[Unit]
Description=TikTok Live Recorder UI and Backend
After=network.target

[Service]
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/ttl-downloader
EnvironmentFile=/opt/ttl-downloader/.env
ExecStart=/opt/ttl-downloader/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable ttl-downloader
sudo systemctl start ttl-downloader
sudo systemctl status ttl-downloader
```

After future code updates, use:

```bash
cd /opt/ttl-downloader
git pull
sudo systemctl restart ttl-downloader
sudo systemctl status ttl-downloader
```

### 10. Put Nginx in front

Create:

```bash
sudo nano /etc/nginx/sites-available/ttl-downloader
```

Use:

```nginx
server {
    listen 80;
    server_name your-domain-or-server-ip;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable it:

```bash
sudo ln -s /etc/nginx/sites-available/ttl-downloader /etc/nginx/sites-enabled/ttl-downloader
sudo nginx -t
sudo systemctl reload nginx
```

If you serve the app under a subpath such as `/tiktok`, set `ROOT_PATH=/tiktok` in `.env` and use a matching Nginx location:

```nginx
location /tiktok/ {
    proxy_pass http://127.0.0.1:8000/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Then verify:

```bash
curl https://your-domain.com/tiktok/health
curl https://your-domain.com/tiktok/health/details
curl https://your-domain.com/tiktok/favicon.svg
```

### 11. Optional: add HTTPS

If you have a domain:

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

## Notes

- This app allows only one active recording at a time.
- Watch mode can monitor multiple accounts, but recording still starts only when the single recording slot is available.
- The recorder is invoked through subprocess and follows the upstream CLI contract.
- Finished files are stored locally.
- Downloading a finished recording removes the file afterward.
- Private or restricted lives may require a valid TikTok session.
- Invalid or empty form submissions are surfaced with readable browser messages.
- Corrupt `jobs.json` or `watch_jobs.json` files are backed up and reset automatically so the app can recover.

## Credits

- UI and backend: this repository
- Recording engine: [Michele0303/tiktok-live-recorder](https://github.com/Michele0303/tiktok-live-recorder)
