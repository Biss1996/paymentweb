# Operator setup and operating guide

**For Vercel, use VERCEL.md instead of the single-host deployment instructions below.** Vercel requires PostgreSQL and scheduled HTTP delivery, both supported in this version. CLI diagnostics and payment reconciliation commands remain applicable.

## Scope and architecture

Main site backend → authenticated checkout API → hosted payment page → HashPay STK API. HashPay → signed incoming callback → atomic payment success + durable event → background worker → main-site signed webhook. Browser return links are independent of payment confirmation.

This is a single-host, low-volume implementation using local SQLite with WAL. For a persistent-server deployment, use one delivery worker to avoid unnecessary polling; database leases also protect against overlapping delivery invocations. Multiple API workers are supported. Use a local persistent disk, not an ephemeral/serverless filesystem, shared network drive or independent containers with different databases. Higher-volume, multi-host production needs PostgreSQL plus leased/locked delivery jobs. This package does not claim to be a licensed payment aggregator, implement payouts or provide settlement accounting.

## Before connecting real payments

1. Obtain your authorized HashPay payment channel/account ID, secret API key, and the signing secret for the webhook endpoint you configure. Confirm that STK API access remains available for your account because the published low-level endpoints are being phased out. If the provider requires its replacement API, the adapter must be updated and tested before live use.
2. Confirm with the provider the authorized receiving Till/Paybill, account ownership and intended business use. Funds follow that provider channel; the software does not move or distribute them.
3. Deploy the API and **one worker** with the same durable database. Set public DNS and valid HTTPS. Register `https://YOUR_PAYMENT_DOMAIN/webhooks/hashpay` in HashPay Settings for the configured channel. A separate webhook secret may apply to global versus channel-specific endpoints.
4. Create a separate live environment and merchant credentials. Do not use the demo database. Set:

```dotenv
PAYMENT_MODE=live
PUBLIC_BASE_URL=https://payments.your-domain.com
DATABASE_PATH=/data/payments.db
HASHPAY_API_KEY=YOUR_PRIVATE_PROVIDER_KEY
HASHPAY_ACCOUNT_ID=YOUR_CHANNEL_ID
HASHPAY_WEBHOOK_SECRET=YOUR_ENDPOINT_SIGNING_SECRET
HASHPAY_STK_ENABLED=yes
```

5. Provision the merchant with their exact HTTPS webhook and return URLs. URLs are an operator trust boundary: verify ownership and route them only to intended public endpoints. In live mode, apply an outbound firewall rule denying private, loopback, metadata and internal network ranges while allowing the provider and approved merchant destination(s). This prevents a compromised or misconfigured URL/DNS entry reaching internal infrastructure. HTTP redirects are disabled for deliveries.
6. Perform a small real payment under your control only when you are ready to spend it. Verify the actual handset business name, expected amount, signed callback payload/reference, database success, main-site webhook, order fulfilment and receiving account entry. Test interruption/retry scenarios before opening checkout to customers.

## Docker deployment

```bash
cp .env.example .env
# Edit .env using the live settings above.
docker compose up --build -d
# Replace sample URLs with the main-site owner's actual endpoints:
docker compose exec api python manage.py merchant --name 'Main Store' --webhook https://main-site.com/api/payments/ruthpay/webhook --return-url https://main-site.com/orders
docker compose exec api python manage.py status
docker compose logs --tail 100 api worker
```

Compose binds the API to localhost:3001. Place an HTTPS reverse proxy in front, using `nginx.conf` as a template. Persistent volume deletion would lose payment records; do not run `docker compose down -v` on live data. Without Docker, use `gunicorn --bind 127.0.0.1:3001 --workers 2 --threads 2 wsgi:app` and a system service for `python manage.py worker`.

## Secrets, access and backups

The merchant API key is stored only as a SHA-256 digest; its high-entropy original is shown once. Webhook signing keys must remain recoverable for HMAC signing and are stored in the local database. Protect the database/volume and backups with host access controls and disk encryption; use a secret manager and encryption at rest before a broader multi-tenant deployment. Provider secrets reside in environment configuration. Never publish `.env`, production databases or CLI credential output. Checkout URLs are bearer credentials. Avoid logging URL paths at the reverse proxy. Database connections enforce foreign keys and a busy timeout; transactions are parameterized.

Use synchronized system clocks. Back up SQLite using its online backup API (not copying only the main file while WAL writes are active). An example, with a protected backup directory:

```bash
python -c "import sqlite3; s=sqlite3.connect('/data/payments.db'); d=sqlite3.connect('/protected-backups/payments.db'); s.backup(d); d.close(); s.close()"
```

Keep backups outside the application host, restrict access and perform a restore drill. Protect phone-number records under your data retention/access policy. No automated retention cleanup is included.

For merchant credential replacement, disable the old merchant using `python manage.py disable MERCHANT_ID`, provision a new one and coordinate the migration with the owner. Existing queued payment events still need their old webhook signing secret until delivered; disabling blocks new payments but does not discard payment notifications. Overlapping API-key rotation/self-service onboarding is not implemented.

## Monitoring and incident response

- Poll `/health` for web/database availability. This is **not** a provider/worker health check.
- Run `python manage.py status` to inspect the latest 50 payments and delivery records. Monitor all undelivered events and PROCESSING payments older than 5 minutes in your production monitoring. Add alerts for worker process exit, disk usage, 5xx and repeated delivery errors. These alerts require your hosting/monitoring setup.
- Delivery attempts are persisted, capped at 12 and delayed exponentially. When the owner fixes their receiver, replay using `python manage.py replay EVENT_ID`. Run only one worker; after a crash, an already delivered event may be sent again. Main-site idempotency is mandatory.
- A provider initiation timeout leaves PROCESSING. The request may have reached M-Pesa. Do not send another prompt or create a replacement checkout until reconciled.
- If the signed success callback is missing, obtain the receipt from your authorized provider records, then run `python manage.py reconcile PAYMENT_ID --receipt ACTUAL_RECEIPT`. This makes an authenticated provider PULL lookup and requires matching receipt, amount and the internal payment ID as bill reference before settling and queueing the notification. A screenshot or customer claim alone cannot settle an order.
- If PULL returns a different/truncated bill reference or lacks required fields, this tool refuses settlement. Resolve the evidence with HashPay support; never bypass the checks.
- Invalid signatures, account/phone/amount/checkout mismatches and reused receipts are rejected. Investigate configuration and provider evidence rather than suppressing validation. The reference sent to HashPay is the gateway's internal payment UUID, not the main-site order ID.
- Payment expiry controls new initiation only. A late verified success still settles the original payment; the main site must support late delivery.

## Current limits to account for

- One provider collection account per deployment. All provisioned merchant identities share that destination. Deploy separate instances for separate collection accounts.
- Live automatic settlement only handles the documented success callback. Failure/cancellation event contracts are not assumed. Other provider events are acknowledged without changing payment state. Cancelled/failed/uncertain live attempts therefore remain PROCESSING for operator reconciliation; there is no customer self-service retry until a provider-confirmed retry workflow is implemented.
- Only one STK attempt per order/session, no amount edits and no automated refunds or reversals. A confirmed cancellation/failure needs operator/provider review before a replacement merchant order reference is issued. Never create a replacement while the first might have succeeded.
- No reconciliation scheduler, automatic alerts, accounting dashboard, bank payouts, commissions, multi-currency, public merchant signup or fully automated key rotation.
- Nginx rate limits below are a starting point. Tune using legitimate traffic and the provider's current account limits. The app itself does not have a distributed rate limiter.
- The example main site is a runnable reference, not a drop-in replacement for authentication, order ownership, inventory or your site's full security controls.

## Technical sources

Official HashBack/HashPay documentation: https://www.hashback.co.ke/documentation (accessed 23 October 2026). Verify provider changes before release. This project's outbound webhook API is defined in INTEGRATION-GUIDE.md and does not copy the provider's callback contract.
