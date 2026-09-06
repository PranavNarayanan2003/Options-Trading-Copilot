# Free always-on deployment — Oracle Cloud Always Free

This is the recommended zero-hosting-cost route for V1.4.2. OpenAI API usage and any Webull OpenAPI market-data subscription are separate from cloud hosting and may not be free.

## Recommended VM

- Oracle Cloud Infrastructure (OCI) home region: Singapore if available when you create the tenancy.
- Shape: `VM.Standard.A1.Flex` (Always Free eligible).
- OCPU: 1.
- Memory: 4 GB.
- Ubuntu 24.04 LTS ARM64.
- Boot disk: default ~50 GB.
- One bot instance only.

## A. Create an SSH key on Windows

```powershell
ssh-keygen -t ed25519 -C "options-copilot"
Get-Content "$env:USERPROFILE\.ssh\id_ed25519.pub"
```

Copy the displayed public key into OCI when creating the VM. Never upload the private `id_ed25519` file.

## B. Create the OCI VM

In Oracle Cloud Console:

1. Compute → Instances → Create instance.
2. Select Ubuntu 24.04.
3. Select the Always Free eligible `VM.Standard.A1.Flex` shape.
4. Configure 1 OCPU / 4 GB RAM.
5. Use your SSH public key.
6. Leave the default boot volume near 50 GB.
7. Record the VM's public IPv4 address.

For the private-dashboard deployment in this guide, you only need inbound TCP 22 (SSH). The bot itself makes outbound HTTPS/WebSocket connections to Alpaca, OpenAI, Webull and Telegram.

## C. Connect from Windows

```powershell
ssh ubuntu@YOUR_ORACLE_PUBLIC_IP
```

If your OCI image uses a different username, use the username shown by Oracle for that image.

## D. Install Docker on Ubuntu

Run on the Oracle VM:

```bash
sudo apt update
sudo apt install -y ca-certificates curl unzip
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo ${UBUNTU_CODENAME:-$VERSION_CODENAME}) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker $USER
```

Log out and SSH back in, then verify:

```bash
docker --version
docker compose version
```

## E. Upload V1.4.2 from Windows

On your Windows PC, from a separate PowerShell window:

```powershell
scp "C:\path\to\ai-options-trading-copilot-v1.4.2.zip" ubuntu@YOUR_ORACLE_PUBLIC_IP:/home/ubuntu/
```

On the Oracle VM:

```bash
sudo mkdir -p /opt/options-copilot
sudo chown -R $USER:$USER /opt/options-copilot
cd /opt/options-copilot
unzip /home/ubuntu/ai-options-trading-copilot-v1.4.2.zip
cd ai-options-trading-copilot-v1.4.2
```

## F. Configure production secrets

```bash
cp .env.example .env
nano .env
```

Minimum recommended live configuration:

```env
DATA_MODE=live

ALPACA_API_KEY=...
ALPACA_API_SECRET=...
ALPACA_STOCK_FEED=iex
ALPACA_OPTION_FEED=indicative

ENABLE_TELEGRAM=true
TELEGRAM_BOT_TOKEN=...
TELEGRAM_BOOTSTRAP_CHAT_ID=

ENABLE_NEWS=true
TRADING_ECONOMICS_API_KEY=

OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5.6-terra
OPENAI_REASONING_EFFORT=none
OPENAI_TIMEOUT_SECONDS=3
ENABLE_AI_REVIEW=true

ENABLE_WEBULL_QUOTES=true
WEBULL_QUOTES_REQUIRED_FOR_READY=false
WEBULL_APP_KEY=...
WEBULL_APP_SECRET=...
WEBULL_API_ENDPOINT=api.webull.com
WEBULL_ACCESS_TOKEN=
WEBULL_OPTION_SNAPSHOT_PATH=/market-data/options/snapshots/list
WEBULL_MAX_QUOTE_AGE_SECONDS=20

# Enable if you also want your own Webull positions/fills synchronized.
ENABLE_WEBULL_SYNC=true
WEBULL_ACCOUNT_ID=...
WEBULL_POLL_SECONDS=15

PUBLIC_BASE_URL=
ADMIN_TOKEN=GENERATE_A_LONG_RANDOM_SECRET
DATABASE_PATH=/data/alerts.sqlite3
ENABLE_BROKER_EXECUTION=false
```

Generate an admin token:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

## G. Run diagnostics before starting the bot

Because Docker has not built yet, you can either run diagnostics after building or install Python dependencies directly. The simplest production check is:

```bash
docker compose build
docker compose run --rm copilot python scripts/check_setup.py
```

Do not continue until Alpaca, AI and Telegram pass. If Webull option quotes fail, leave `WEBULL_QUOTES_REQUIRED_FOR_READY=false` until the Webull OpenAPI market-data permission is resolved.

## H. Run tests in the production container

```bash
docker compose run --rm copilot python -m pytest -q
```

## I. Start the always-on service

```bash
docker compose up -d
docker compose ps
docker compose logs --tail=200
curl http://127.0.0.1:8000/health
```

The Compose file uses `restart: unless-stopped` and a persistent `/data` volume.

## J. Access the dashboard for free without exposing it publicly

V1.4.2 binds port 8000 only to the server's localhost. On Windows run:

```powershell
ssh -L 8000:127.0.0.1:8000 ubuntu@YOUR_ORACLE_PUBLIC_IP
```

Leave that SSH window open and browse locally to:

```text
http://127.0.0.1:8000
```

Telegram continues working even when this SSH tunnel is closed. The tunnel is only for viewing the dashboard.

## K. Daily operation

You do not run `run_live.py` on your laptop after cloud deployment. Docker keeps the bot running on OCI. Useful commands:

```bash
cd /opt/options-copilot/ai-options-trading-copilot-v1.4.2
docker compose ps
docker compose logs -f --tail=200
docker compose restart
```

## L. Important single-instance rule

Once the cloud service is live, stop your local live bot. The current design uses one Telegram long-polling consumer, SQLite state and one alert/session state machine. Do not run local and cloud live instances simultaneously.

## M. Upgrades

Never run `docker compose down -v`; `-v` deletes the persistent database volume.

Normal upgrade flow:

```bash
docker compose down
docker compose build --no-cache
docker compose up -d
```
