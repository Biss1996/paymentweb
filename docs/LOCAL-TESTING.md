# Run and test locally in VS Code + WSL

Use WSL terminals inside VS Code. Open `~/development/code/se-prep/private/payment-service` and run `code .` from that directory. Commands below assume every terminal is in that project directory. Python 3.12+ is required; the demo main site also needs Node 24+.

## 1. One-time setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# Create .env once; preserve an existing .env if you already configured it.
cp -n .env.example .env
```

For local mock testing, set these entries in `.env`:

```dotenv
PAYMENT_MODE=mock
PUBLIC_BASE_URL=http://localhost:3001
DATABASE_PATH=./data/payments.db
DATABASE_URL=
CRON_SECRET=
```

Leave provider credentials empty in mock mode. A blank DATABASE_URL selects local SQLite.

```bash
set -a
source .env
set +a
python manage.py migrate
python -m unittest discover -s tests -v
python manage.py merchant --name 'Main Store' --webhook http://localhost:4000/webhook --return-url http://localhost:4000/
```

Save the printed merchant API key and webhook secret privately; you need them in terminal 3. The test suite uses isolated temporary databases and mocks provider calls. The real-HTTP end-to-end test requires Node 24+ and free port 4000; run tests before starting the example site.

## 2. Terminal 1 — gateway

```bash
source .venv/bin/activate
set -a; source .env; set +a
python -m flask --app wsgi:app run --host 127.0.0.1 --port 3001
```

Open http://localhost:3001/health and expect `{"status":"ok","mode":"mock"}`. The home page at http://localhost:3001 explains how checkout works; the Pay form is created from the main site.

## 3. Terminal 2 — local webhook worker

```bash
source .venv/bin/activate
set -a; source .env; set +a
python manage.py worker
```

This local process sends saved payment events to the example main site. On Vercel it is replaced by scheduled HTTP invocations; see VERCEL.md.

## 4. Terminal 3 — example main site

```bash
export GATEWAY_URL=http://localhost:3001
export GATEWAY_API_KEY='PASTE_MERCHANT_API_KEY'
export GATEWAY_WEBHOOK_SECRET='PASTE_MERCHANT_WEBHOOK_SECRET'
node examples/main-site.mjs
```

Open http://localhost:4000. Click Pay with M-Pesa, enter a Kenyan test phone number such as 0712345678 and submit. The page stays Processing until simulation completes. In mock mode no STK prompt or charge occurs.

## 5. Terminal 4 — simulate successful payment

```bash
source .venv/bin/activate
set -a; source .env; set +a
python manage.py status
python manage.py simulate PAYMENT_ID_SHOWN_ABOVE
```

The worker sends the signed webhook. Checkout shows Payment received. Return to the main site and it shows Paid. The main site updates only after verifying the event and checking the gateway's authenticated status.

## 6. Verify webhook retries

For a fresh unpaid order, stop the example main site (Ctrl+C), then simulate success. Inspect `python manage.py status`: the event has a network error and a scheduled retry. Restart the main site with the same credentials; the worker delivers on the next attempt. For an already delivered event, `python manage.py replay EVENT_ID` queues the same immutable event again; the main site accepts it without paying/fulfilling twice.

The demo uses one fixed order, DEMO-1001. To repeat from scratch, stop all processes and back up then remove **only your disposable mock** `data/payments.db`, its `-wal`/`-shm` files if present, and `main-site-demo.db`; restart setup and provision fresh merchant credentials. Never do this to live data. Alternatively change the demo order reference in `examples/main-site.mjs` and create a fresh order record.

## Troubleshooting

- `No module named venv` / ensurepip error: install your distribution's Python venv package, then create the virtual environment again.
- `No module named flask` / psycopg: activate `.venv` and rerun `pip install -r requirements.txt`.
- Node SQLite import error: check `node --version`; this runnable demo requires Node 24+.
- 401 from gateway: the demo's GATEWAY_API_KEY must match the merchant API key, not the HashPay key.
- Payment successful but main site unpaid: inspect worker terminal, merchant URL, signing secret and `python manage.py status`. Both localhost ports must be reachable from WSL.
- Port 4000 occupied during tests: stop the example main site first. The end-to-end test needs that port.
- Wrong environment: `.env` is not auto-loaded. Source it in each Python terminal as shown.
- Keep `.env`, production databases, the demo database and merchant credentials out of Git.
