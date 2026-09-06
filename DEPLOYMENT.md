# Deployment — V1.4.2

Use one persistent Docker instance. For a zero-hosting-cost test deployment, follow `FREE_CLOUD_DEPLOYMENT.md` (Oracle Always Free).

Basic server commands:

```bash
cp .env.example .env
# configure secrets
docker compose build
docker compose run --rm copilot python scripts/check_setup.py
docker compose run --rm copilot python -m pytest -q
docker compose up -d
docker compose ps
docker compose logs --tail=200
curl http://127.0.0.1:8000/health
```

The dashboard binds to `127.0.0.1:8000` by default. Use an SSH tunnel for free/private access:

```bash
ssh -L 8000:127.0.0.1:8000 ubuntu@SERVER_IP
```

Then open `http://127.0.0.1:8000` on your local machine.

State is stored in the named Docker volume at `/data/alerts.sqlite3`. Never use `docker compose down -v` unless you intentionally want to erase subscriber/alert/trade history.

Run one live replica only.
