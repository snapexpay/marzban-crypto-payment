# Криптоплатёжный шлюз для Marzban — приём USDT (TRC20) за VPN-подписки

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)

Небольшой self-hosted сервис для продажи подписок [Marzban](https://github.com/Gozargah/Marzban) за криптовалюту через [SnapEX Pay](https://snapex.pro). Магазин или Telegram-бот запрашивает ссылку на оплату. Клиент платит на странице SnapEX: USDT в сетях TRC20, ERC20 и BEP20 на любую сумму, BTC, ETH, LTC и TRX — и на меньшие суммы, если позволяет комиссия сети, а в счетах от $8 (текущий минимум) — также USDT в Solana, TON и других сетях и другими монетами. После оплаты счёта сервис создаёт или продлевает пользователя в панели Marzban и возвращает ссылку подписки. Свой кошелёк и блокчейн-ноду поднимать не нужно.

[English version](README.md)

> **Подключаетесь к SnapEX Pay?** Мерчант-аккаунт, тарифы и все готовые модули — на **[pay.snapex.pro](https://pay.snapex.pro)**.

## Возможности

- `POST /v1/checkout` создаёт счёт SnapEX Pay по тарифу и возвращает `pay_url`.
- `/v1/checkout` и `/v1/orders/*` требуют bearer-токен `API_TOKEN` (сравнение в постоянном времени), поэтому создавать заказы и читать ссылки подписки могут только ваш бот или магазин.
- Вебхук проверяется по подписи (`HMAC-SHA256`), окно timestamp — 5 минут, сравнение в постоянном времени.
- Телу вебхука сервис не доверяет: счёт перечитывается через API SnapEX, пользователь выдаётся только при статусе `paid`.
- Идемпотентность: повторные и параллельные доставки вебхука выдают подписку по счёту ровно один раз.
- Новый пользователь получает все протоколы, для которых в панели есть inbound. Существующий продлевается от текущей даты окончания (или от «сейчас», если срок уже истёк). Для тарифов с трафиком новая квота добавляется поверх израсходованного.
- Тарифы задаются одной переменной окружения, заказы хранятся в SQLite.
- `GET /v1/orders/{invoice_id}` возвращает статус заказа и ссылку подписки, поэтому бот может его опрашивать.

## Как это работает

```
магазин / бот ── POST /v1/checkout ──▶ сервис ── POST /invoices ──▶ SnapEX Pay
клиент        ◀──────────── pay_url ────────────────────────────────┘
SnapEX Pay ── POST /webhook (подпись) ──▶ сервис ── GET /invoices/{id} ──▶ SnapEX Pay
                                             └── status=paid ──▶ API Marzban: создать / продлить
магазин / бот ── GET /v1/orders/{invoice_id} ──▶ subscription_url
```

## Быстрый старт (Docker Compose)

```bash
curl -fsSLO https://pay.snapex.pro/downloads/marzban/snapexpay-marzban-1.1.0.zip
unzip snapexpay-marzban-1.1.0.zip
cd snapexpay-marzban-1.1.0
cp .env.example .env
# заполнить SNAPEX_API_KEY, SNAPEX_WEBHOOK_SECRET, API_TOKEN (openssl rand -hex 32),
# MARZBAN_URL и логин/пароль админа Marzban
docker compose up -d
curl http://127.0.0.1:8080/health
```

Контейнер слушает порт 8080, а `orders.sqlite` хранит в `./data`. Поставьте перед ним HTTPS reverse proxy (nginx, Caddy, Traefik): SnapEX Pay отправляет вебхуки только на `https://`.

Без Docker (Python 3.11+):

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
set -a && . ./.env && set +a      # DATA_DIR должен указывать на доступную для записи папку, например ./data
python -m app.main                # слушает $PORT
```

## Настройки

Все переменные, которые читает код. Список совпадает с [`.env.example`](.env.example).

| Переменная | Обязательна | По умолчанию | Описание |
|---|---|---|---|
| `SNAPEX_API_KEY` | да | — | API-ключ мерчанта (`smk_…`) с правами `invoices.read` и `invoices.write`. |
| `SNAPEX_WEBHOOK_SECRET` | да | — | Секрет подписи (`whsec_…`) вашего webhook-эндпоинта в кабинете SnapEX. |
| `SNAPEX_API_URL` | нет | `https://snapex.pro/api/v1/merchant` | Базовый URL Merchant API. |
| `SNAPEX_ENVIRONMENT` | нет | `live` | `live` или `test`. Тестовые счета не двигают реальные деньги и автоматически становятся оплаченными вскоре после выбора способа оплаты. |
| `SNAPEX_FEE_ON` | нет | `merchant` | Кто платит комиссию SnapEX: `merchant` или `customer`. |
| `SNAPEX_PARTNER_ID` | нет | — | Partner ID интегратора (реферальная программа SnapEX Pay). Оставьте пустым, если ставите сами. |
| `SNAPEX_EXPIRES_MINUTES` | нет | `60` | Срок жизни счёта в минутах, ограничен диапазоном от 1 минуты до 24 часов. |
| `SNAPEX_USER_AGENT` | нет | `SnapEX-Pay-Integration/1.1.0` | User-Agent для запросов к SnapEX. |
| `API_TOKEN` | да | — | Bearer-токен, который бот или магазин передаёт в `/v1/checkout` и `/v1/orders/*`. Сгенерировать: `openssl rand -hex 32`. Если не задан, эти эндпоинты отвечают `503`. |
| `MARZBAN_URL` | да | — | URL панели, например `https://panel.example.com` (API находится под `/api`). |
| `MARZBAN_USERNAME` | да\* | — | Логин админа Marzban. |
| `MARZBAN_PASSWORD` | да\* | — | Пароль админа Marzban. |
| `MARZBAN_TOKEN` | нет\* | — | Готовый токен админа вместо логина и пароля. |
| `MARZBAN_SUB_URL` | нет | `MARZBAN_URL` | Публичный адрес, который подставляется к относительным ссылкам `/sub/<token>`. Marzban отдаёт такие ссылки, если у него пуст `XRAY_SUBSCRIPTION_URL_PREFIX`. |
| `PLANS` | нет | `month:30:5.00:0,quarter:90:12.00:0` | Тарифы через запятую в формате `id:дни:usd:байты_трафика`, `0` = безлимит. |
| `PORT` | нет | `8080` | Порт для `python -m app.main`. Docker-образ всегда слушает 8080. |
| `DATA_DIR` | нет | `./data` (`/data` в Docker) | Папка для `orders.sqlite`. |

\* Нужен либо `MARZBAN_TOKEN`, либо `MARZBAN_USERNAME` + `MARZBAN_PASSWORD`. Токены Marzban истекают, поэтому для долгой работы надёжнее логин и пароль.

Пример с тарифом на 100 ГБ:

```
PLANS=month:30:5.00:0,quarter:90:12.00:107374182400
```

## Настройка SnapEX Pay

1. Войдите на [snapex.pro](https://snapex.pro), создайте API-ключ с правами `invoices.read` и `invoices.write` и запишите его в `SNAPEX_API_KEY`.
2. Добавьте webhook-эндпоинт `https://pay.example.com/webhook` с событием `invoice.paid`. События `invoice.partially_paid` и `invoice.expired` подключать не обязательно, они только обновляют статус заказа. Секрет подписи запишите в `SNAPEX_WEBHOOK_SECRET`.
3. Перезапустите сервис.

Вебхук проверяется по заголовкам `Webhook-Timestamp` и `Webhook-Signature: v1=<hex>`, где подпись равна `HMAC-SHA256(secret, "<timestamp>.<сырое тело>")`. На запросы старше 5 минут или с неверной подписью сервис отвечает `401`. Если Marzban вернул ошибку, сервис отвечает `502`, и SnapEX повторит доставку позже.

## API

Создать платёж:

```bash
curl -sS -X POST https://pay.example.com/v1/checkout \
  -H "Authorization: Bearer $API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"plan":"month","username":"alice","success_url":"https://shop.example.com/thanks"}'
```

```json
{"invoice_id":"INV-7KQ2M9XA","pay_url":"https://snapex.pro/pay/…","username":"alice","amount_usd":"5.00"}
```

Поля запроса:
- `plan` — обязательное.
- `username` — необязательное. Если его не передать, сгенерируется случайное имя `u…`. Действуют правила Marzban: 3–32 символа из букв, цифр и `_ - @ .`.
- `success_url`, `cancel_url`.
- `telegram_id`, `email` — только сохраняются вместе с заказом.

Проверить заказ:

```bash
curl -sS -H "Authorization: Bearer $API_TOKEN" https://pay.example.com/v1/orders/INV-7KQ2M9XA
```

`status` остаётся `open`, пока не придёт вебхук. Дальше он повторяет статус счёта в SnapEX (`awaiting`, `underpaid`, `expired`, `partially_paid`, …) и становится `paid`, когда пользователь в Marzban готов. `GET /v1/plans` возвращает список тарифов.

Готовый Telegram-бот для Marzban, 3x-ui и Remnawave: [telegram-vpn-bot-crypto-payment](https://pay.snapex.pro/integrations/telegram-vpn-bot).

## Тесты

```bash
python -m venv /tmp/venv && /tmp/venv/bin/pip install -r requirements.txt pytest
/tmp/venv/bin/python -m pytest -q tests
```

Все HTTP-запросы в тестах замоканы. Покрыты проверка подписи вебхука, идемпотентная выдача, формат запроса на создание счёта и запросы create/modify к Marzban. Чтобы проверить всё без реальных денег, задайте `SNAPEX_ENVIRONMENT=test`.

## FAQ

**`401 bad webhook signature` или `stale or missing webhook timestamp`.** Проверьте, что `SNAPEX_WEBHOOK_SECRET` относится именно к этому эндпоинту, а часы сервера синхронизированы (NTP). Прокси не должен менять тело запроса.

**`502 Marzban login failed`.** Проверьте `MARZBAN_URL` (без `/api` и `/dashboard`) и логин/пароль админа.

**`Marzban has no inbounds configured`.** Добавьте хотя бы один inbound в конфиг Xray панели.

**Не тот хост в `subscription_url`.** Задайте `XRAY_SUBSCRIPTION_URL_PREFIX` в Marzban или `MARZBAN_SUB_URL` здесь.

**Клиент оплатил, а заказ остался `open`.** Посмотрите журнал доставки вебхуков в кабинете SnapEX и проверьте, что URL доступен по HTTPS. Если счёт `partially_paid` (клиент заплатил меньше нужной суммы), подписка не выдаётся, такой случай нужно разобрать вручную.

**`401 invalid API token` или `503 API_TOKEN is not set` от `/v1/checkout`.** Задайте `API_TOKEN` в `.env`, перезапустите сервис и передавайте токен в заголовке `Authorization: Bearer <API_TOKEN>`. Храните токен только на бэкенде бота или магазина и никогда не кладите его в код для браузера: с ним можно создавать заказы на любой `username` и читать их ссылки подписки. `/webhook` и `/health` токен не используют, вебхук защищён подписью.

**Что происходит с существующим пользователем?** Срок продлевается на число дней тарифа, начиная с более поздней из двух дат: «сейчас» или текущая дата окончания. Статус становится `active`, даже если админ отключил пользователя вручную. Для тарифа с трафиком новая квота добавляется к большему из двух значений: израсходованный трафик или текущий лимит. Тариф с `0` делает трафик безлимитным.

## Другие интеграции SnapEX Pay

Все модули, тарифы и регистрация: **[pay.snapex.pro](https://pay.snapex.pro)**.

- [3x-ui crypto payment](https://pay.snapex.pro/integrations/3x-ui)
- [Xboard / V2Board crypto payment](https://pay.snapex.pro/integrations/xboard)
- [Remnawave crypto payment](https://pay.snapex.pro/integrations/remnawave)
- [Telegram VPN-бот с криптооплатой](https://pay.snapex.pro/integrations/telegram-vpn-bot)
- [FOSSBilling crypto payment](https://pay.snapex.pro/integrations/fossbilling)
- [Paymenter crypto payment](https://pay.snapex.pro/integrations/paymenter)
- [n8n-нода SnapEX Pay](https://pay.snapex.pro/integrations/n8n)

## Лицензия

[MIT](LICENSE) · [snapex.pro](https://snapex.pro)
