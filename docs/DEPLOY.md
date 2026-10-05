# Deploying Transit

This guide puts the platform on **one Linux VM**:
- **Postgres** stores the data.
- **The API** runs as one process.
- **Caddy** serves the web app and the API over HTTPS, with certificates renewed automatically.

Files used: [deploy/docker-compose.prod.yml](../deploy/docker-compose.prod.yml), [deploy/Caddyfile](../deploy/Caddyfile),
[deploy/.env.prod.example](../deploy/.env.prod.example), [deploy/backup.sh](../deploy/backup.sh) and
[backend/Dockerfile](../backend/Dockerfile).

```
 phones / browsers ──HTTPS──► Caddy :443 ─┬─ transit.example.edu      → web app (static files)
                                          └─ api.transit.example.edu  → api:8000 (REST + /ws)
                                                                          │
                                                                          ▼
                                                                    db (Postgres, not exposed)
```

## Rule 1: exactly one API process
The WebSocket hub, the event bus and the background jobs all run inside the API process: the
delay watcher, the trip generator, the attendance repair job and the clean-up jobs. **Don't** start
uvicorn with `--workers 2`, **don't** run `docker compose up --scale api=2`, and don't put two VMs
behind a load balancer. If you did:
- every background job would run twice (duplicate alerts);
- live updates would only reach the clients connected to the same process;
- the rate limits would be per process.

To scale out later, move the hub and events to Redis or Postgres `LISTEN/NOTIFY` first.

One API process handles a college of a few thousand students comfortably. Logins hash passwords
in a worker thread, and dashboards use a fixed number of queries whatever the number of trips.

## 1. Prepare the VM
- **Machine:** Ubuntu 22.04 or later with at least 2 vCPU and 4 GB RAM. With the local AI model,
  use 4 vCPU and 8 GB RAM; a GPU is optional.
- **Software:** Docker Engine with the compose plugin installed.
- **DNS:** two A/AAAA records pointing at the VM, for example `transit.example.edu` and
  `api.transit.example.edu`.
- **Firewall:** open ports 80 and 443 only.

```sh
sudo mkdir -p /opt/transit && sudo chown "$USER" /opt/transit
git clone https://github.com/Santhosh-Kumar-ctrl/Transport-System-Final.git /opt/transit
cd /opt/transit/deploy
cp .env.prod.example .env
chmod 600 .env
```
Edit `deploy/.env`:
- Set both domains.
- Generate the two secrets: `openssl rand -hex 24` for `POSTGRES_PASSWORD` and
  `openssl rand -hex 32` for `JWT_SECRET`.
- Leave `ALLOW_SIMULATION=false`.

The API **refuses to start** in production if:
- `JWT_SECRET` is the development default or shorter than 32 characters;
- `ALLOW_SIMULATION` is on;
- `CORS_ORIGINS` is `*`. The compose file sets it to `https://$WEB_DOMAIN` for you.

## 2. Build the web app
Build on any machine with Flutter, then copy `frontend/build/web` to the same path on the VM:
```sh
cd frontend
flutter build web --release \
  --dart-define=API_BASE=https://api.transit.example.edu \
  --dart-define=TILE_URL='https://api.maptiler.com/maps/streets-v2/{z}/{x}/{y}.png?key=YOUR_KEY' \
  --dart-define=TILE_ATTRIBUTION='© MapTiler © OpenStreetMap contributors'
```
The public OpenStreetMap tile server is not allowed for production traffic. Use MapTiler,
Stadia or your own tile server.

## 3. Start it
```sh
cd /opt/transit/deploy
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml logs -f api      # "Application startup complete"
curl https://api.transit.example.edu/health                # {"status":"ok",...}
```
The API runs `alembic upgrade head` every time it starts, so updates migrate the database
automatically. Create the first admin, then do everything else in the app:
```sh
docker compose -f docker-compose.prod.yml exec api python -c "
import asyncio
from app.core.db import SessionLocal
from app.core.roles import Role
from app.modules.auth import service
from app.modules.auth.schemas import UserCreate
async def main():
    async with SessionLocal() as s:
        await service.create_user(s, UserCreate(email='office@college.edu', password='CHANGE-ME-NOW',
                                                full_name='Transport Office', role=Role.ADMIN), actor_id=None)
        await s.commit()
asyncio.run(main())"
```
Don't run `scripts.seed` in production: it creates demo accounts with a known password.

### Optional: the AI report agent
Without it, reports are checked by the built-in rules. To use the local model:
```sh
docker compose -f docker-compose.prod.yml --profile ai up -d
docker compose -f docker-compose.prod.yml exec ollama ollama pull qwen3:4b
docker compose -f docker-compose.prod.yml exec ollama ollama pull nomic-embed-text
# then set REPORT_AI=ollama in deploy/.env and restart the API:
docker compose -f docker-compose.prod.yml up -d api
```
The models take about 3 GB of disk and 4 to 5 GB of RAM while loaded. Each report takes about
6 s on a CPU.

## 4. Backups
```sh
chmod +x /opt/transit/deploy/backup.sh
sudo crontab -e     # add the line below
30 2 * * * /opt/transit/deploy/backup.sh >> /var/log/transit-backup.log 2>&1
```
Backups go to `/var/backups/transit`, and 14 days are kept (`BACKUP_DIR`, `BACKUP_KEEP_DAYS`).
Copy them off the VM as well, for example with your cloud provider's volume snapshots or `rclone`.
Restore commands are in the script header. Try a restore before go-live.

## 5. Updating
```sh
cd /opt/transit && git pull
docker compose -f deploy/docker-compose.prod.yml up -d --build api
```
Deploy outside the morning and evening runs. The API restarts in a few seconds, and the apps
reconnect on their own. If the server stops between a trip ending and its attendance being
written, the attendance repair job writes it within 5 minutes of coming back.

## 6. Monitoring
- **Health:** `GET https://api.<domain>/health` returns 200, or 503 when the database is
  unreachable. Point an uptime checker at it.
- **Logs:** `docker compose -f deploy/docker-compose.prod.yml logs api`. Tokens never appear in
  the logs, because the WebSocket token is sent in a frame, not the URL.
- **Disk:** GPS fixes are kept for 90 days and read notifications for 180 days
  (`POSITION_RETENTION_DAYS`, `NOTIFICATION_RETENTION_DAYS`). The event history is kept for good.

## 7. Android release
1. Create an upload key once and keep it safe. Losing it means you can't update the app.
   ```sh
   keytool -genkey -v -keystore ~/transit-upload.jks -keyalg RSA -keysize 2048 -validity 10000 -alias upload
   ```
2. Create `frontend/android/key.properties`. It is git-ignored; never commit it.
   ```
   storePassword=...
   keyPassword=...
   keyAlias=upload
   storeFile=C:/Users/you/transit-upload.jks
   ```
3. Build:
   ```sh
   flutter build appbundle --release --dart-define=API_BASE=https://api.transit.example.edu --dart-define=TILE_URL=...
   ```
Release builds refuse plain `http://` traffic. Only debug builds may talk to a laptop's dev API.
Without `key.properties`, release builds are signed with the debug key: fine for testing, but they
can't be published.

## Trying the production stack locally
Set `WEB_DOMAIN=localhost` and `API_DOMAIN=api.localhost` in `deploy/.env`, build the web app with
`API_BASE=https://api.localhost`, and start the stack. Caddy signs both names with its own local
certificate authority, so browsers warn once. Use `curl -k` or trust Caddy's root certificate.
