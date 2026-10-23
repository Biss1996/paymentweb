# Deploy from Git to Vercel

First test locally with `docs/LOCAL-TESTING.md`. Your Vercel project root must be the directory containing `wsgi.py`, `pyproject.toml`, `requirements.txt` and `vercel.json` (usually `payment-service`). This is a Python Flask backend, not a Vite build. The entrypoint is explicitly configured in `pyproject.toml`. Static files are served from `public/static` on Vercel.

## Persistent database

Vercel deployments require PostgreSQL. Create a Neon or Supabase database and obtain a pooled PostgreSQL connection string with TLS. Set `DATABASE_URL` to that connection string in Vercel's environment settings. Keep it secret and use separate preview/test and production databases. Do not commit `.env`.

The local SQLite file is not used on Vercel; startup deliberately refuses ephemeral database storage. Existing local mock payments are not copied to production.

Before deployment, initialize PostgreSQL from your WSL machine, using that database's credentials in a private environment:

```bash
source .venv/bin/activate
# Set DATABASE_URL privately, or source a protected .env file containing it.
python manage.py migrate
```

Migrations are explicit. Serverless cold starts do not create tables or modify schema. Database schema upgrades and backup policy are your responsibility; schema scripts currently create missing tables without destructive changes.

## Environment settings in Vercel

| Name | Value |
|---|---|
| DATABASE_URL | Pooled PostgreSQL URL with TLS |
| PAYMENT_MODE | `mock` for the first deployment, `live` after live configuration |
| PUBLIC_BASE_URL | Stable HTTPS production URL or custom payment domain |
| CRON_SECRET | A private random string of at least 32 characters |
| HASHPAY_API_KEY | Provider key, required for live mode |
| HASHPAY_ACCOUNT_ID | Collection channel ID, required for live mode |
| HASHPAY_WEBHOOK_SECRET | Signing secret for this callback endpoint, required for live mode |
| HASHPAY_STK_ENABLED | `yes` only after confirming live STK access |

Generate a CRON_SECRET using `python -c 'import secrets; print(secrets.token_urlsafe(40))'` and copy it privately. Environment changes require a new deployment. Do not enable live credentials on public preview deployments.

## Webhook delivery scheduler — required before live use

The provider success callback saves an outgoing event transactionally, then attempts immediate delivery with a 3-second timeout. If it fails, the event remains in the database. Retries are performed by the protected endpoint `GET /api/cron/deliver`, which accepts `Authorization: Bearer YOUR_CRON_SECRET`.

- **Vercel Pro/Enterprise:** replace `vercel.json` with `deploy/vercel-pro.json` to enable a once-per-minute Vercel cron job. Set `CRON_SECRET`; Vercel includes it in the Authorization header.
- **Other scheduler:** retain the default `vercel.json`, which deliberately defines no cron. Configure a trusted external scheduler to call the same endpoint every minute with the secret header. Make sure your hosting plan permits your intended business use.
- Do not rely on Vercel Hobby's daily cron interval for timely payment retries. A deployment without either scheduler is incomplete for live use.

Each invocation handles up to four events within a bounded time budget. Database-backed 90-second leases prevent simultaneous invocations from sending an event concurrently. A process killed after delivery but before saving the acknowledgement may still cause later duplicate delivery, so main-site event deduplication remains mandatory. Provision throughput and monitoring for the expected traffic; four retry jobs per minute is a small-volume starting point.

## Git and deployment

Open the project in VS Code, review changes, and commit/push to your connected Git repository. Ensure the Vercel root directory is set correctly. No environment files, databases or merchant credential files should appear in the commit. Git pushes trigger deployments when your repository is connected to the project.

After deployment:

1. Verify `https://YOUR_DOMAIN/health` returns `status: ok` and the intended mode. This tests database connectivity; it does not certify HashPay access or the scheduler.
2. Provision a merchant against the production PostgreSQL database using `python manage.py merchant --name 'Main Store' --webhook https://MAIN_SITE/api/payments/ruthpay/webhook --return-url https://MAIN_SITE/orders`. Exchange the printed credentials privately. The main site needs its own webhook URL; do not use this gateway's HashPay endpoint as the merchant receiver.
3. In HashPay Settings, register `https://YOUR_DOMAIN/webhooks/hashpay` and configure its signing secret in Vercel. Partner accounts can alternatively run `python manage.py register-webhook --partner-account`; this updates the URL only, so obtain the corresponding signing secret from Settings.
4. Configure the scheduler and confirm delivery/retry behavior. Check Vercel deployment protection: HashPay callbacks and checkout must be reachable without Vercel login. Keep application HMAC checks enabled; do not substitute a login page for callback authentication.
5. Switch to live only with the correct provider credentials and endpoint access. Run a controlled small payment, verify the receiving channel and amount, and confirm the main site's paid status. No real payment was run during development.
6. Give the owner `docs/INTEGRATION-GUIDE.md`, your stable payment domain and their merchant credentials. Never share your HashPay key or CRON_SECRET with the main site.

## Provider diagnostics

`python manage.py provider-status PAYMENT_ID` calls the documented HashPay transaction-status endpoint and prints diagnostic results. It cannot mark an order paid because that response alone may lack amount/receipt evidence. `python manage.py reconcile PAYMENT_ID --receipt RECEIPT` performs authenticated receipt verification and exact reference/amount matching before settlement.

Unknown/cancellation provider events still require reconciliation; their contracts are not invented. No automated second charge or replacement order is created for uncertain outcomes.

Official references checked 23 October 2026:
- https://vercel.com/docs/frameworks/backend/flask
- https://vercel.com/docs/cron-jobs/usage-and-pricing
- https://www.hashback.co.ke/documentation
