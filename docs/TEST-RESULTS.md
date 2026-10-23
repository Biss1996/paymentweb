# Validation — 23 October 2026

Command: `python -m unittest discover -s tests -v`

Result: **21 tests passed** on Python 3.12.14 / Node v24.19.0.

Coverage includes:

- Real local HTTP checkout initialization by the supplied Node main-site example.
- Browser redirect URL, hosted checkout rendering and mock payment initiation.
- Payment settlement, durable event delivery and the main site's database changing to Paid.
- Duplicate event delivery accepted without processing a second time.
- Authentication, merchant isolation, amount validation and checkout expiry.
- Rejected provider signatures, amount/account/checkout/phone mismatches and reused receipts.
- Repeated checkout initialization returning the existing payment and conflicting amounts rejected.
- Delivery retry scheduling and raw-byte outbound webhook signatures.
- Live startup guard and uncertain STK request outcome preventing another prompt.
- Unknown provider events leaving payment unresolved rather than declaring success or failure.

Live HashPay credentials were not provided. No actual STK prompt, live callback, receiving account settlement, production proxy, Docker build or hosting deployment was tested. The included end-to-end test completes payment in mock mode; provider-specific behavior still needs account-level acceptance testing before live release.

Vercel-specific tests cover protected cron authorization, event leasing across overlapping invocations, lease recovery, retry limits and refusing an ephemeral database. PostgreSQL driver/schema code is included but a real external PostgreSQL connection and Vercel deployment have not been tested.
