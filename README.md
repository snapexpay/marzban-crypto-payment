# Marzban Crypto Payment Gateway — accept USDT (TRC20) for VPN subscriptions

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)

A small self-hosted service that lets you sell [Marzban](https://github.com/Gozargah/Marzban) VPN subscriptions for crypto through [SnapEX Pay](https://snapex.pro). Your shop or Telegram bot asks it for a checkout link, the customer pays on the SnapEX hosted payment page (USDT on TRC20, ERC20 or BEP20 at any amount; BTC, ETH, LTC and TRX can also be offered on smaller invoices when the network fee allows; on invoices from $8, the current minimum, also USDT on Solana, TON and other networks and other coins), and once the invoice is paid the service creates or extends the user in your Marzban panel and returns the subscription link. You don't need to run a wallet or a blockchain node.

[Русская версия](README.ru.md)

> **Connecting to SnapEX Pay?** Create a merchant account, check the pricing and see every ready-made module on **[pay.snapex.pro](https://pay.snapex.pro)**.

## Features

- `POST /v1/checkout` creates a SnapEX Pay invoice for a plan and returns `pay_url`.
- `/v1/checkout` and `/v1/orders/*` require a bearer `API_TOKEN` (constant-time compare), so only your bot or shop can create orders and read subscription links.
- Signed webhook (`HMAC-SHA256`, 5-minute timestamp window, constant-time compare).
- Never trusts the webhook body: the invoice is re-read from the SnapEX API and the user is provisioned only when the status is `paid`.
- Idempotent: repeated or concurrent webhook deliveries provision an invoice only once.
- New users get every protocol that has an inbound in your panel. Existing users are extended from their current expiry (or from now if they have already expired), and traffic plans add a fresh quota on top of their usage.
- Plans are set with one environment variable. Orders are kept in SQLite.
- `GET /v1/orders/{invoice_id}` returns the order status and the subscription URL, so a bot can poll it.

## How it works

```
shop / Telegram bot ── POST /v1/checkout ──▶ this service ── POST /invoices ──▶ SnapEX Pay
customer           ◀───────── pay_url ─────────────────────────────────────────┘
SnapEX Pay ── POST /webhook (signed) ──▶ this service ── GET /invoices/{id} ──▶ SnapEX Pay
                                              └── status=paid ──▶ Marzban API: create / extend user
shop / bot ── GET /v1/orders/{invoice_id} ──▶ subscription_url
```

## Quick start (Docker Compose)

```bash
curl -fsSLO https://pay.snapex.pro/downloads/marzban/snapexpay-marzban-1.1.0.zip
unzip snapexpay-marzban-1.1.0.zip
cd snapexpay-marzban-1.1.0
cp .env.example .env
# set SNAPEX_API_KEY, SNAPEX_WEBHOOK_SECRET, API_TOKEN (openssl rand -hex 32),
# MARZBAN_URL and the Marzban admin credentials
docker compose up -d
curl http://127.0.0.1:8080/health
```

The container listens on port 8080 and stores `orders.sqlite` in `./data`. Put it behind an HTTPS reverse proxy (nginx, Caddy, Traefik), because SnapEX Pay delivers webhooks only to `https://` URLs.

Without Docker (Python 3.11+):

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
set -a && . ./.env && set +a      # with DATA_DIR pointing to a writable folder, e.g. ./data
python -m app.main                # listens on $PORT
```

## Configuration

Every variable the code reads. They match [`.env.example`](.env.example).

| Variable | Required | Default | Description |
|---|---|---|---|
| `SNAPEX_API_KEY` | yes | — | Merchant API key (`smk_…`) with the `invoices.read` and `invoices.write` scopes. |
| `SNAPEX_WEBHOOK_SECRET` | yes | — | Signing secret (`whsec_…`) of your webhook endpoint in the SnapEX cabinet. |
| `SNAPEX_API_URL` | no | `https://snapex.pro/api/v1/merchant` | Merchant API base URL. |
| `SNAPEX_ENVIRONMENT` | no | `live` | `live` or `test`. Test invoices move no real funds and are marked paid automatically shortly after a payment method is chosen. |
| `SNAPEX_FEE_ON` | no | `merchant` | Who pays the SnapEX service fee: `merchant` or `customer`. |
| `SNAPEX_PARTNER_ID` | no | — | Integrator's Partner ID (SnapEX Pay referral program). Leave empty if you set this up yourself. |
| `SNAPEX_EXPIRES_MINUTES` | no | `60` | Invoice lifetime in minutes, clamped to 1 minute … 24 hours. |
| `SNAPEX_USER_AGENT` | no | `SnapEX-Pay-Integration/1.1.0` | User-Agent sent to the SnapEX API. |
| `API_TOKEN` | yes | — | Bearer token your bot or shop sends to `/v1/checkout` and `/v1/orders/*`. Generate it with `openssl rand -hex 32`. If it is unset, those endpoints return `503`. |
| `MARZBAN_URL` | yes | — | Marzban panel URL, e.g. `https://panel.example.com` (the API lives under `/api`). |
| `MARZBAN_USERNAME` | yes\* | — | Marzban admin username. |
| `MARZBAN_PASSWORD` | yes\* | — | Marzban admin password. |
| `MARZBAN_TOKEN` | no\* | — | A ready admin access token, used instead of username/password. |
| `MARZBAN_SUB_URL` | no | `MARZBAN_URL` | Public origin added to relative `/sub/<token>` links. Marzban returns relative links when its `XRAY_SUBSCRIPTION_URL_PREFIX` is empty. |
| `PLANS` | no | `month:30:5.00:0,quarter:90:12.00:0` | Comma-separated `id:days:usd:traffic_bytes`; `0` bytes = unlimited. |
| `PORT` | no | `8080` | Listening port for `python -m app.main`. The Docker image always listens on 8080. |
| `DATA_DIR` | no | `./data` (`/data` in Docker) | Folder for `orders.sqlite`. |

\* Set either `MARZBAN_TOKEN` or `MARZBAN_USERNAME` + `MARZBAN_PASSWORD`. Tokens issued by Marzban expire, so username/password is the better choice for a long-running service.

Example with a 100 GB traffic plan:

```
PLANS=month:30:5.00:0,quarter:90:12.00:107374182400
```

## SnapEX Pay setup

1. Sign in at [snapex.pro](https://snapex.pro) and create an API key with `invoices.read` and `invoices.write`. Put it into `SNAPEX_API_KEY`.
2. Add a webhook endpoint `https://pay.example.com/webhook` subscribed to at least `invoice.paid` (`invoice.partially_paid` and `invoice.expired` are optional and only update the order status). Copy its signing secret into `SNAPEX_WEBHOOK_SECRET`.
3. Restart the service.

The webhook handler checks the `Webhook-Timestamp` and `Webhook-Signature: v1=<hex>` headers, where the signature is `HMAC-SHA256(secret, "<timestamp>.<raw body>")`. Requests older than 5 minutes or with a bad signature get `401`. A Marzban error returns `502`, which makes SnapEX retry the delivery later.

## API

Create a payment:

```bash
curl -sS -X POST https://pay.example.com/v1/checkout \
  -H "Authorization: Bearer $API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"plan":"month","username":"alice","success_url":"https://shop.example.com/thanks"}'
```

```json
{"invoice_id":"INV-7KQ2M9XA","pay_url":"https://snapex.pro/pay/…","username":"alice","amount_usd":"5.00"}
```

Body fields: `plan` (required), `username` (optional: a random `u…` name is generated if you leave it out; Marzban rules apply, 3–32 characters from letters, digits and `_ - @ .`), `success_url`, `cancel_url`, `telegram_id`, `email` (the last two are only stored with the order).

Check the order:

```bash
curl -sS -H "Authorization: Bearer $API_TOKEN" https://pay.example.com/v1/orders/INV-7KQ2M9XA
```

```json
{"invoice_id":"INV-7KQ2M9XA","status":"paid","username":"alice","subscription_url":"https://panel.example.com/sub/…"}
```

`status` is `open` until a webhook arrives, then follows the SnapEX invoice status (`awaiting`, `underpaid`, `expired`, `partially_paid`, …) and becomes `paid` once the Marzban user is ready. `GET /v1/plans` lists the configured plans.

### Telegram bots

A bot can call `POST /v1/checkout` with `Authorization: Bearer <API_TOKEN>`, send `pay_url` to the user, and poll `GET /v1/orders/{invoice_id}` for the subscription link. If you need a complete bot for Marzban, 3x-ui and Remnawave, see [telegram-vpn-bot-crypto-payment](https://pay.snapex.pro/integrations/telegram-vpn-bot).

## Testing

```bash
python -m venv /tmp/venv && /tmp/venv/bin/pip install -r requirements.txt pytest
/tmp/venv/bin/python -m pytest -q tests
```

The tests mock all HTTP calls. They cover webhook signature checks, idempotent provisioning, the invoice request shape and the Marzban create/modify payloads. For a test without real funds, set `SNAPEX_ENVIRONMENT=test`.

## FAQ / troubleshooting

**`401 bad webhook signature` or `stale or missing webhook timestamp`.** Check that `SNAPEX_WEBHOOK_SECRET` belongs to this endpoint and that the server clock is synced (NTP). Your reverse proxy must pass the request body through unchanged.

**`502 Marzban login failed`.** Check `MARZBAN_URL` (without `/api` or `/dashboard`) and the admin credentials. The admin must be allowed to manage the users this service creates.

**`Marzban has no inbounds configured`.** Add at least one inbound to the Xray config of the panel.

**`subscription_url` starts with the wrong host.** Set `XRAY_SUBSCRIPTION_URL_PREFIX` in Marzban, or set `MARZBAN_SUB_URL` here.

**The customer paid but the order stays `open`.** Check the webhook delivery log in the SnapEX cabinet and confirm the URL is reachable over HTTPS. If the invoice is `partially_paid` (the customer sent less than the amount), nothing is provisioned. Settle it manually.

**`401 invalid API token` or `503 API_TOKEN is not set` from `/v1/checkout`.** Set `API_TOKEN` in `.env`, restart, and send it as `Authorization: Bearer <API_TOKEN>`. Keep the token on your bot or shop backend, never in browser code, because it allows creating orders for any username and reading their subscription links. `/webhook` and `/health` don't use the token. The webhook is authenticated by its signature.

**What happens to a user who already exists?** Their expiry is extended by the plan's days, starting from the later of now and the current expiry. The user is set back to `active`, even if an admin had disabled them. For traffic plans the new quota is added to the larger of the user's usage and current limit, and plans with `0` traffic make the user unlimited.

## Other SnapEX Pay integrations

All modules, pricing and sign-up: **[pay.snapex.pro](https://pay.snapex.pro)**.

- [3x-ui crypto payment](https://pay.snapex.pro/integrations/3x-ui)
- [Xboard / V2Board crypto payment](https://pay.snapex.pro/integrations/xboard)
- [Remnawave crypto payment](https://pay.snapex.pro/integrations/remnawave)
- [Telegram VPN bot with crypto payment](https://pay.snapex.pro/integrations/telegram-vpn-bot)
- [FOSSBilling crypto payment](https://pay.snapex.pro/integrations/fossbilling)
- [Paymenter crypto payment](https://pay.snapex.pro/integrations/paymenter)
- [n8n community node for SnapEX Pay](https://pay.snapex.pro/integrations/n8n)

## License

[MIT](LICENSE) · [snapex.pro](https://snapex.pro)
