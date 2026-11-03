"""SQLite mapping from SnapEX invoices to panel checkouts."""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Any


def db_path() -> Path:
    path = Path(os.environ.get("DATA_DIR", "./data"))
    path.mkdir(parents=True, exist_ok=True)
    return path / "orders.sqlite"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            invoice_id TEXT PRIMARY KEY,
            external_id TEXT NOT NULL,
            plan_id TEXT NOT NULL,
            username TEXT,
            telegram_id INTEGER,
            email TEXT,
            amount_usd TEXT NOT NULL,
            status TEXT NOT NULL,
            subscription_url TEXT,
            created_at INTEGER NOT NULL,
            paid_at INTEGER
        )
        """
    )
    conn.commit()
    return conn


def save_order(row: dict[str, Any]) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO orders
            (invoice_id, external_id, plan_id, username, telegram_id, email, amount_usd, status, subscription_url, created_at, paid_at)
            VALUES (:invoice_id, :external_id, :plan_id, :username, :telegram_id, :email, :amount_usd, :status, :subscription_url, :created_at, :paid_at)
            """,
            {
                "subscription_url": None,
                "paid_at": None,
                "created_at": int(time.time()),
                **row,
            },
        )


def get_order(invoice_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        found = conn.execute("SELECT * FROM orders WHERE invoice_id = ?", (invoice_id,)).fetchone()
    return dict(found) if found else None


def mark_paid(invoice_id: str, subscription_url: str | None) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE orders SET status = 'paid', subscription_url = ?, paid_at = ? WHERE invoice_id = ?",
            (subscription_url, int(time.time()), invoice_id),
        )


def claim_for_provisioning(invoice_id: str) -> bool:
    """Atomically move an order to 'provisioning'; False if it is already paid or in progress."""
    with connect() as conn:
        cursor = conn.execute(
            "UPDATE orders SET status = 'provisioning' WHERE invoice_id = ? AND status NOT IN ('paid', 'provisioning')",
            (invoice_id,),
        )
    return cursor.rowcount == 1


def set_status(invoice_id: str, status: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE orders SET status = ? WHERE invoice_id = ? AND status NOT IN ('paid', 'provisioning')",
            (status, invoice_id),
        )


def release(invoice_id: str, status: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE orders SET status = ? WHERE invoice_id = ? AND status = 'provisioning'", (status, invoice_id))
