# Ruthwest Payments — hosted M-Pesa checkout

**Updated for Vercel:** start with [local testing](docs/LOCAL-TESTING.md), then follow [Git → Vercel deployment](docs/VERCEL.md). Vercel uses PostgreSQL and scheduled delivery; the local SQLite/worker instructions below apply only to WSL or a persistent server.

A runnable small-volume payment middleware project with Flask, SQLite locally / PostgreSQL on Vercel, hosted checkout, HashPay adapter, durable outgoing webhooks, merchant provisioning CLI and a Node main-site integration example.

**Delivery state:** working mock environment and tested live adapter logic. Not deployed, not connected to your HashPay account, and not live-payment certified. HashPay's published legacy STK API is marked as being phased out. Confirm endpoint access and callback fields with HashPay before activating live mode. No real payment was initiated while developing this project.

## Contents

- `app.py`, `schema.sql`: merchant-scoped API, hosted checkout, raw-body HashPay HMAC validation and atomic success/outbox persistence.
- `manage.py`: private merchant provisioning, delivery worker, diagnostics, reconciliation, replay and disabling merchants.
- `examples/main-site.mjs`: runnable main-site order/redirect/webhook/status example with persistent event deduplication.
- `docs/INTEGRATION-GUIDE.md`: hand this to the main-site owner/developer.
- `docs/OPERATIONS.md`: deployment, credentials, incidents and limitations.
- `compose.yaml`, `Dockerfile`, `docs/nginx.conf`: deployment starting points.
- `tests/`: verification tests; run `python -m unittest discover -s tests -v`.

## Local setup — Linux / WSL / macOS

Python 3.12+; Node 24+ only for the example main site. Run inside this project directory:

```bash
umask 077
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
set -a
source .env
set +a
python manage.py merchant --name 'Main Store' --webhook http://localhost:4000/webhook --return-url http://localhost:4000/
```

The last command prints a merchant ID, API key and webhook secret **once**. Store them privately. This CLI is the merchant administration interface; there is no public admin registration endpoint.

Start the payment API in terminal 1:

```bash
source .venv/bin/activate
set -a; source .env; set +a
python -m flask --app wsgi:app run --host 127.0.0.1 --port 3001
```

Start the notification worker in terminal 2:

```bash
source .venv/bin/activate
set -a; source .env; set +a
python manage.py worker
```

Start the example main site in terminal 3, using the two merchant credentials printed earlier:

```bash
export GATEWAY_URL=http://localhost:3001
export GATEWAY_API_KEY='PASTE_MERCHANT_API_KEY'
export GATEWAY_WEBHOOK_SECRET='PASTE_MERCHANT_WEBHOOK_SECRET'
node examples/main-site.mjs
```

Open **http://localhost:4000**, click Pay, enter a test Kenyan number and click Pay with M-Pesa. In mock mode no phone prompt or charge occurs. Use terminal 4 to complete the payment:

```bash
source .venv/bin/activate
set -a; source .env; set +a
python manage.py status
python manage.py simulate PAYMENT_ID_FROM_STATUS
```

The worker delivers the signed event to the example main site. Checkout changes to Payment received, and the main site shows Paid on return or in another tab. To simulate a failure, use `python manage.py simulate PAYMENT_ID --result failed`. The fixed demo order can be paid once; for another clean demo stop the example and delete its **demo-only** `main-site-demo.db`, then restart it with a fresh gateway mock database or different demo order ID.

Environment variables are **not** loaded automatically from `.env`. Source it as above or use Docker Compose. Development Flask is for local testing; production uses Gunicorn and HTTPS. PowerShell users can set the same variables with `$env:NAME='value'`; Gunicorn deployment should run in Linux/WSL/Docker.

## Main-site handoff

Give the owner `docs/INTEGRATION-GUIDE.md` and, if useful, `examples/main-site.mjs`. Privately provide gateway URL, merchant API key and webhook signing secret. They implement one checkout-initiation call and a signed webhook receiver in their existing backend, then poll their own order status from the browser.

## Provider documentation

Adapter implemented against the official documentation retrieved on 23 October 2026:
https://www.hashback.co.ke/documentation

Live requests use `POST https://api.hashback.co.ke/initiatestk`; receipt reconciliation uses `POST https://api.hashback.co.ke/v1/pullapi`. Provider webhooks use `X-Hashpay-Signature: sha256=…` over raw body bytes. Main-site webhooks use this project's separate `X-RuthPay-*` contract. The provider's status-only acknowledgement is deliberately not sufficient for settlement because it may lack amount/receipt evidence.
# paymentweb
