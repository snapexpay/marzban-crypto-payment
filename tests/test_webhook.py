import hashlib
import hmac
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main, marzban, snapex, store
from app.snapex import SnapexError, verify_webhook

SECRET = "whsec_test"
API_TOKEN = "api-token-test"


def api() -> TestClient:
    return TestClient(main.app, headers={"Authorization": f"Bearer {API_TOKEN}"})


def sign(body: str, ts: str | None = None, secret: str = SECRET) -> dict[str, str]:
    ts = ts or str(int(time.time()))
    sig = "v1=" + hmac.new(secret.encode(), f"{ts}.{body}".encode(), hashlib.sha256).hexdigest()
    return {"Webhook-Timestamp": ts, "Webhook-Signature": sig}


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("SNAPEX_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("SNAPEX_API_KEY", "smk_test_dummy")
    monkeypatch.setenv("API_TOKEN", API_TOKEN)
    monkeypatch.setenv("SNAPEX_API_URL", "https://snapex.test/api/v1/merchant")
    monkeypatch.setenv("MARZBAN_URL", "https://panel.test")
    monkeypatch.setenv("MARZBAN_TOKEN", "mz-token")
    monkeypatch.setenv("PLANS", "month:30:5.00:0,small:30:3:1000")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MARZBAN_SUB_URL", raising=False)


class Seen(list):
    """Requests the app sent; `routes` maps (method, path) to a canned response."""

    def __init__(self):
        super().__init__()
        self.routes: dict[tuple[str, str], object] = {}


@pytest.fixture
def http(monkeypatch):
    """Route every httpx.Client the app opens to `routes` instead of the network."""
    seen = Seen()
    routes = seen.routes
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        reply = routes.get((request.method, request.url.path))
        if reply is None:
            return httpx.Response(404, json={"detail": "not found"})
        return reply(request) if callable(reply) else reply

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)
    return seen


# --- signature -------------------------------------------------------------


def test_webhook_signature():
    body = json.dumps({"type": "invoice.paid", "data": {"invoice_id": "INV-1"}})
    event = verify_webhook(sign(body), body)
    assert event["type"] == "invoice.paid"


def test_webhook_signature_over_raw_bytes_with_unicode():
    body = json.dumps({"type": "invoice.paid", "data": {"description": "Подписка"}}, ensure_ascii=False)
    assert verify_webhook(sign(body), body.encode())["data"]["description"] == "Подписка"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda h, b: ({**h, "Webhook-Signature": "v1=" + "0" * 64}, b),
        lambda h, b: (h, b.replace("INV-1", "INV-2")),
        lambda h, b: ({**h, "Webhook-Signature": h["Webhook-Signature"][3:]}, b),
        lambda h, b: (sign(b, secret="whsec_other"), b),
        lambda h, b: ({"Webhook-Signature": h["Webhook-Signature"]}, b),
        lambda h, b: ({**h, "Webhook-Signature": "v1=é"}, b),
    ],
)
def test_webhook_signature_rejected(mutate):
    body = json.dumps({"type": "invoice.paid", "data": {"invoice_id": "INV-1"}})
    headers, payload = mutate(sign(body), body)
    with pytest.raises(SnapexError):
        verify_webhook(headers, payload)


def test_webhook_stale_timestamp_rejected():
    body = json.dumps({"type": "invoice.paid", "data": {"invoice_id": "INV-1"}})
    with pytest.raises(SnapexError, match="stale"):
        verify_webhook(sign(body, ts=str(int(time.time()) - 301)), body)


# --- invoice creation ------------------------------------------------------


def test_checkout_sends_expected_invoice_request(http):
    http.routes[("POST", "/api/v1/merchant/invoices")] = httpx.Response(
        201, json={"data": {"invoice_id": "INV-ABCD1234", "status": "open", "pay_url": "https://snapex.test/pay/tok"}}
    )
    client = api()
    res = client.post("/v1/checkout", json={"plan": "month", "username": "alice", "success_url": "https://shop.test/ok"})
    assert res.status_code == 200, res.text
    assert res.json() == {"invoice_id": "INV-ABCD1234", "pay_url": "https://snapex.test/pay/tok", "username": "alice", "amount_usd": "5.00"}

    [request] = http
    assert str(request.url) == "https://snapex.test/api/v1/merchant/invoices"
    assert request.headers["Authorization"] == "Bearer smk_test_dummy"
    assert request.headers["Content-Type"] == "application/json"
    body = json.loads(request.content)
    assert request.headers["Idempotency-Key"] == body["external_id"]
    assert body["external_id"].startswith("marzban-alice-month-")
    assert body == {
        "amount_usd": "5.00",
        "fee_on": "merchant",
        "environment": "live",
        "external_id": body["external_id"],
        "description": "Marzban month for alice",
        "success_url": "https://shop.test/ok",
        "expires_in": 3600,
    }
    assert store.get_order("INV-ABCD1234")["status"] == "open"


def test_checkout_surfaces_validation_error(http):
    http.routes[("POST", "/api/v1/merchant/invoices")] = httpx.Response(
        422, json={"message": "Amount is below the 0.50 USD minimum.", "errors": {"amount_usd": ["..."]}}
    )
    res = api().post("/v1/checkout", json={"plan": "month", "username": "alice"})
    assert res.status_code == 502
    assert "below" in res.json()["detail"]


@pytest.mark.parametrize("username", ["ab", "a-bc", "abc?x=1", "abc/def", "x" * 33, "ab cd"])
def test_checkout_rejects_usernames_marzban_would_refuse(http, username):
    res = api().post("/v1/checkout", json={"plan": "month", "username": username})
    assert res.status_code == 422
    assert http == []


# --- API token --------------------------------------------------------------


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "api-token-tes"}])
def test_checkout_and_orders_require_api_token(http, headers):
    client = TestClient(main.app, headers=headers)
    assert client.post("/v1/checkout", json={"plan": "month", "username": "alice"}).status_code == 401
    assert client.get("/v1/orders/INV-1").status_code == 401
    assert http == []


def test_api_disabled_without_api_token(http, monkeypatch):
    monkeypatch.setenv("API_TOKEN", "")
    client = api()
    assert client.post("/v1/checkout", json={"plan": "month", "username": "alice"}).status_code == 503
    assert client.get("/v1/orders/INV-1").status_code == 503
    assert TestClient(main.app).get("/health").status_code == 200
    assert http == []


# --- webhook processing / idempotency --------------------------------------


def seed_order(invoice_id="INV-1", plan_id="month", username="alice"):
    store.save_order(
        {
            "invoice_id": invoice_id,
            "external_id": f"marzban-{username}-{plan_id}-x",
            "plan_id": plan_id,
            "username": username,
            "telegram_id": None,
            "email": None,
            "amount_usd": "5.00",
            "status": "open",
        }
    )


def post_event(client, event_type="invoice.paid", invoice_id="INV-1"):
    body = json.dumps({"id": "evt", "type": event_type, "data": {"invoice_id": invoice_id, "status": "paid"}})
    return client.post("/webhook", content=body, headers={**sign(body), "Content-Type": "application/json"})


def test_paid_webhook_provisions_once(http, monkeypatch):
    seed_order()
    http.routes[("GET", "/api/v1/merchant/invoices/INV-1")] = httpx.Response(200, json={"data": {"invoice_id": "INV-1", "status": "paid"}})
    calls = []
    monkeypatch.setattr(marzban, "provision", lambda u, d, t: calls.append((u, d, t)) or "https://panel.test/sub/tok")
    client = api()

    assert post_event(client).text == "ok"
    assert post_event(client).text == "already processed"
    assert post_event(client, "invoice.partially_paid").text == "already processed"
    assert calls == [("alice", 30, 0)]
    order = client.get("/v1/orders/INV-1").json()
    assert order == {"invoice_id": "INV-1", "status": "paid", "username": "alice", "subscription_url": "https://panel.test/sub/tok"}


def test_body_status_is_not_trusted(http, monkeypatch):
    seed_order()
    http.routes[("GET", "/api/v1/merchant/invoices/INV-1")] = httpx.Response(200, json={"data": {"invoice_id": "INV-1", "status": "partially_paid"}})
    monkeypatch.setattr(marzban, "provision", lambda *a: pytest.fail("must not provision"))
    client = api()
    assert post_event(client).text == "not paid yet"
    assert client.get("/v1/orders/INV-1").json()["status"] == "partially_paid"


def test_concurrent_claim_only_once():
    seed_order()
    assert store.claim_for_provisioning("INV-1") is True
    assert store.claim_for_provisioning("INV-1") is False


def test_marzban_failure_releases_claim_for_retry(http, monkeypatch):
    seed_order()
    http.routes[("GET", "/api/v1/merchant/invoices/INV-1")] = httpx.Response(200, json={"data": {"invoice_id": "INV-1", "status": "paid"}})

    def boom(*a):
        raise marzban.MarzbanError("down")

    monkeypatch.setattr(marzban, "provision", boom)
    client = api()
    assert post_event(client).status_code == 502
    assert store.get_order("INV-1")["status"] == "open"
    monkeypatch.setattr(marzban, "provision", lambda *a: "https://panel.test/sub/tok")
    assert post_event(client).text == "ok"


def test_webhook_bad_signature_is_401(http):
    seed_order()
    body = json.dumps({"type": "invoice.paid", "data": {"invoice_id": "INV-1"}})
    res = api().post("/webhook", content=body, headers=sign(body, secret="whsec_wrong"))
    assert res.status_code == 401
    assert http == []


def test_unknown_and_non_invoice_events_are_acknowledged(http):
    client = api()
    assert post_event(client, invoice_id="INV-404").text == "unknown invoice"
    assert post_event(client, event_type="payout.completed").text == "ignored"


# --- Marzban API usage -----------------------------------------------------


def test_new_user_gets_only_enabled_protocols(http):
    http.routes[("GET", "/api/inbounds")] = httpx.Response(200, json={"shadowsocks": [{"tag": "SS"}], "vless": []})
    http.routes[("POST", "/api/user")] = httpx.Response(200, json={"username": "bob", "subscription_url": "/sub/abc"})
    before = int(time.time())
    url = marzban.provision("bob", 30, 1000)
    assert url == "https://panel.test/sub/abc"
    create = json.loads(http[-1].content)
    assert http[-1].headers["Authorization"] == "Bearer mz-token"
    assert create["proxies"] == {"shadowsocks": {}}
    assert create["data_limit"] == 1000 and create["status"] == "active"
    assert before + 30 * 86400 <= create["expire"] <= int(time.time()) + 30 * 86400


def test_existing_user_is_extended_and_unlimited(http, monkeypatch):
    monkeypatch.setenv("MARZBAN_SUB_URL", "https://sub.test/")
    future = int(time.time()) + 5 * 86400
    http.routes[("GET", "/api/user/carol")] = httpx.Response(200, json={"expire": future, "data_limit": 100, "used_traffic": 100, "status": "limited"})
    http.routes[("PUT", "/api/user/carol")] = httpx.Response(200, json={"subscription_url": "/sub/xyz"})
    assert marzban.provision("carol", 30, 0) == "https://sub.test/sub/xyz"
    modify = json.loads(http[-1].content)
    assert modify == {"expire": future + 30 * 86400, "data_limit": 0, "status": "active"}


def test_existing_limited_user_gets_fresh_quota(http):
    http.routes[("GET", "/api/user/dave")] = httpx.Response(200, json={"expire": None, "data_limit": 1000, "used_traffic": 1200, "status": "limited"})
    http.routes[("PUT", "/api/user/dave")] = httpx.Response(200, json={"subscription_url": "https://sub.test/sub/d"})
    assert marzban.provision("dave", 30, 1000) == "https://sub.test/sub/d"
    assert json.loads(http[-1].content)["data_limit"] == 2200
