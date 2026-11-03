"""HTTP API: create a SnapEX invoice, provision a Marzban user when it is paid."""

from __future__ import annotations

import os
import re
import secrets
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import marzban, plans, snapex, store

app = FastAPI(title="Marzban crypto payment (SnapEX Pay)", version="1.1.0")
# Safe subset of Marzban's USERNAME_REGEXP (the username also goes into /api/user/{username}).
USERNAME_RE = re.compile(r"^(?=\w{3,32}\b)[A-Za-z0-9_@.\-]+$", re.ASCII)


class CheckoutIn(BaseModel):
    plan: str
    username: str | None = Field(default=None, max_length=32)
    telegram_id: int | None = None
    email: str | None = None
    success_url: str | None = None
    cancel_url: str | None = None


def require_api_token(authorization: str | None = Header(default=None)) -> None:
    """Checkout and order lookups return subscription links, so only your bot/site may call them."""
    expected = os.environ.get("API_TOKEN", "").strip()
    if not expected:
        raise HTTPException(503, "API_TOKEN is not set")
    given = (authorization or "").removeprefix("Bearer ").strip()
    if not secrets.compare_digest(given.encode(), expected.encode()):
        raise HTTPException(401, "invalid API token")


@app.get("/health")
def health() -> dict[str, str]:
    return {"ok": "yes"}


@app.get("/v1/plans")
def list_plans() -> dict[str, Any]:
    return {"plans": plans.parse_plans()}


@app.post("/v1/checkout", dependencies=[Depends(require_api_token)])
def checkout(body: CheckoutIn) -> dict[str, Any]:
    try:
        plan = plans.get_plan(body.plan)
    except KeyError as exc:
        raise HTTPException(404, f"unknown plan: {body.plan}") from exc
    username = (body.username or f"u{secrets.token_hex(4)}").strip()
    if len(username) > 32 or not USERNAME_RE.match(username):
        raise HTTPException(422, "username: 3–32 chars, starts with 3+ letters/digits/_, then letters, digits, _ - @ .")
    external_id = f"marzban-{username}-{plan['id']}-{secrets.token_hex(3)}"
    try:
        created = snapex.create_invoice(
            amount_usd=plan["amount_usd"],
            external_id=external_id,
            description=f"Marzban {plan['id']} for {username}",
            success_url=body.success_url,
            cancel_url=body.cancel_url,
            idempotency_key=external_id,
        )
    except snapex.SnapexError as exc:
        raise HTTPException(502, str(exc)) from exc
    store.save_order(
        {
            "invoice_id": created["invoice_id"],
            "external_id": external_id,
            "plan_id": plan["id"],
            "username": username,
            "telegram_id": body.telegram_id,
            "email": body.email,
            "amount_usd": plan["amount_usd"],
            "status": created.get("status") or "open",
        }
    )
    return {"invoice_id": created["invoice_id"], "pay_url": created["pay_url"], "username": username, "amount_usd": plan["amount_usd"]}


@app.post("/webhook")
async def webhook(
    request: Request,
    webhook_timestamp: str | None = Header(default=None, alias="Webhook-Timestamp"),
    webhook_signature: str | None = Header(default=None, alias="Webhook-Signature"),
) -> PlainTextResponse:
    raw = await request.body()
    try:
        event = snapex.verify_webhook(
            {"Webhook-Timestamp": webhook_timestamp or "", "Webhook-Signature": webhook_signature or ""},
            raw,
        )
    except snapex.SnapexError as exc:
        raise HTTPException(401, str(exc)) from exc
    invoice_id = str((event.get("data") or {}).get("invoice_id") or "")
    event_type = str(event.get("type") or "")
    if not invoice_id or not event_type.startswith("invoice."):
        return PlainTextResponse("ignored")
    return PlainTextResponse(await run_in_threadpool(process_invoice, invoice_id))


def process_invoice(invoice_id: str) -> str:
    """Provision once per invoice. The event body is only a hint; the API is the source of truth."""
    row = store.get_order(invoice_id)
    if not row:
        return "unknown invoice"
    if row["status"] in ("paid", "provisioning"):
        return "already processed"
    try:
        remote = snapex.get_invoice(invoice_id)
    except snapex.SnapexError as exc:
        raise HTTPException(502, str(exc)) from exc
    status = str(remote.get("status") or "")
    if status != "paid":
        if status:
            store.set_status(invoice_id, status)
        return "not paid yet"
    if not store.claim_for_provisioning(invoice_id):
        return "already processed"
    try:
        plan = plans.get_plan(row["plan_id"])
        sub = marzban.provision(row["username"], plan["days"], plan["traffic_bytes"])
    except Exception as exc:
        store.release(invoice_id, row["status"])
        if isinstance(exc, (marzban.MarzbanError, httpx.HTTPError)):
            raise HTTPException(502, f"Marzban: {exc}") from exc
        raise
    store.mark_paid(invoice_id, sub)
    return "ok"


@app.get("/v1/orders/{invoice_id}", dependencies=[Depends(require_api_token)])
def order_status(invoice_id: str) -> dict[str, Any]:
    row = store.get_order(invoice_id)
    if not row:
        raise HTTPException(404, "not found")
    return {
        "invoice_id": row["invoice_id"],
        "status": row["status"],
        "username": row["username"],
        "subscription_url": row["subscription_url"] if row["status"] == "paid" else None,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
