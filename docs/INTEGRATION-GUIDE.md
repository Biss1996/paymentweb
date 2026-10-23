# Main-site owner: M-Pesa integration instructions

This service creates a hosted checkout, sends an M-Pesa prompt through HashPay, verifies payment notifications and reliably informs your backend. Your backend remains responsible for pricing, customer authentication, order ownership and fulfilment. The customer's PIN is entered only into the M-Pesa handset prompt.

## What the payment-service operator supplies

Exchange credentials privately, never in frontend code or a public repository:

| Setting | Example / purpose |
|---|---|
| Gateway URL | `https://payments.your-domain.com` |
| Merchant API key | `rk_…` — a **secret backend credential** |
| Webhook signing secret | A separate secret for verifying notifications |
| Your webhook URL | `https://main-site.com/api/payments/ruthpay/webhook` |
| Your return URL | An existing authenticated order/dashboard page on your site |
| Environment | Mock or live; never mix orders or credentials between them |

The operator registers your webhook and return URLs. They cannot be overridden in checkout requests. Your own HashPay key is not needed by your main-site frontend or backend.

## 1. Create checkout from your backend

Authenticate the customer, verify ownership of the order and read the amount from your database. Send this request **from your server**:

```http
POST /api/v1/checkout/initialize
Authorization: Bearer YOUR_SECRET_MERCHANT_API_KEY
Content-Type: application/json

{"order_id":"ORDER-1001","amount":1500,"currency":"KES"}
```

```json
{
  "id": "gateway-payment-id",
  "order_id": "ORDER-1001",
  "amount": 1500,
  "currency": "KES",
  "status": "PENDING",
  "receipt": null,
  "created": 1792760000,
  "expires": 1792763600,
  "checkout_url": "https://payments.your-domain.com/checkout/OPAQUE_TOKEN"
}
```

Save `id` with your order **before redirecting** the browser to `checkout_url`. The URL contains a bearer token: do not expose it in analytics, logs or third-party scripts. Sessions accept payment initiation for one hour. A payment already initiated can still settle later.

The `(merchant, order_id)` pair is the idempotency key. Network retries with the same amount return the same payment. A changed amount returns 409. Never create a fresh order reference just because the network timed out. Whole KES only, from 1 to 150,000; your account may have lower limits. Only KES is accepted.

## 2. Receive signed payment notifications

A successful payment generates an immutable event delivered to your registered webhook:

```json
{"event_id":"unique-event-id","type":"payment.success","payment_id":"gateway-payment-id","order_id":"ORDER-1001","amount_paid":1500,"currency":"KES","status":"SUCCESSFUL","mpesa_receipt":"M-PESA-RECEIPT","created_at":1792760000}
```

Headers:

```text
X-RuthPay-Timestamp: 1792760005
X-RuthPay-Event: unique-event-id
X-RuthPay-Signature: sha256=HEX_DIGEST
```

Signature algorithm:

```text
signed_bytes = ASCII(timestamp_header) + b'.' + raw_HTTP_body_bytes
expected = 'sha256=' + HMAC_SHA256(webhook_secret, signed_bytes).hex()
```

1. Read and preserve the raw body before any JSON middleware changes it.
2. Require a valid numeric timestamp within 300 seconds of your server's current time; keep server clocks synchronized.
3. Compare the signature in constant time, checking lengths before comparison. Reject invalid signatures with 401.
4. Parse JSON. Require `type=payment.success`, `status=SUCCESSFUL`, `currency=KES`.
5. Match `payment_id`, `order_id`, and exact amount to your saved order. Optionally confirm using the authenticated status endpoint below; the example does this.
6. In **one database transaction**, deduplicate `event_id`, mark the matching order paid, save its receipt and record the processed event. Prevent the same receipt paying two orders.
7. Return 200/204 only after committing. Duplicate events should return 200 without fulfilling twice. If your database is unavailable, return 503 to request another delivery.

If fulfilment involves external work (email, inventory, subscriptions), enqueue it in your own transactional outbox with a unique payment/order key. A database paid flag alone cannot guarantee exactly-once external actions.

Use `examples/main-site.mjs` as a working Node 24+ reference. Its demo order database and localhost endpoints must be replaced with your actual order storage and customer/session authorization. Do not place an API key in browser JavaScript. No CORS configuration is needed: browser → your backend → payment backend.

## 3. Read authoritative payment status

```http
GET /api/v1/payments/GATEWAY_PAYMENT_ID
Authorization: Bearer YOUR_SECRET_MERCHANT_API_KEY
```

Returns the payment fields shown above, without a checkout URL. Requests for another merchant's payment return 404. Your customer-facing status endpoint must authenticate the customer and authorize the order. Have your page poll **your own** status endpoint every 3–5 seconds, stopping when the order is paid. The example demonstrates this.

A return to your site is navigation, never proof of payment. STK initiation success is also not proof of payment. Only verified backend confirmation may release goods or credit an account.

## Statuses and error handling

| Status / response | Required behavior |
|---|---|
| PENDING | Checkout created; prompt not yet requested |
| PROCESSING | Prompt initiated, or outcome uncertain; wait and reconcile |
| SUCCESSFUL | Verified paid and success event durably queued |
| FAILED | Mock simulation failure; contact operator before arranging another attempt |
| 400 | Correct invalid request fields |
| 401 / 403 | Check credentials / suspended merchant; do not loop |
| 404 | Unknown payment or wrong merchant |
| 409 | Conflicting amount or state; investigate |
| 410 | Checkout expired; contact operator |
| 429 / 5xx / network error | Back off; retry initialization with the **same** order ID |

Success webhooks have at-least-once delivery. The delivery processor retries up to 12 attempts with exponential delays capped at one hour (actual timing also depends on the configured scheduler), then leaves the event undelivered for operator replay. Vercel callbacks attempt immediate delivery; failed attempts are retried by the required scheduler. Closing the checkout page does not remove the event. Retries preserve the same body/event ID and generate a fresh signature timestamp. A replay can arrive long after the original payment. Use `event_id` rather than timestamp for deduplication.

## Acceptance checklist with the operator

- Test checkout and confirm the main site turns to Paid without manually editing it.
- Deliver the event twice and confirm only one fulfilment.
- Test wrong amount, invalid signature and another customer's order access.
- Stop your webhook temporarily, pay, restart it and confirm retry delivery.
- Close the checkout tab after initiation and confirm payment still updates.
- Confirm where funds settle and the business name shown on the handset prompt.
- Agree who handles pending payments, refunds, reconciliation and support.

The current service supports one configured HashPay collection channel per deployment. Multiple merchant API identities route funds to that same channel. Independent businesses needing different destination accounts must use separate deployments/channel credentials until per-merchant routing is implemented. There is no wallet, commission deduction, payout engine or automated refund functionality.
