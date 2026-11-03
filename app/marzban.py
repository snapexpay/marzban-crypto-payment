"""Marzban admin API: create or extend a user after payment."""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

TIMEOUT = 20.0


class MarzbanError(RuntimeError):
    pass


def base_url() -> str:
    url = os.environ.get("MARZBAN_URL", "").rstrip("/")
    if not url:
        raise MarzbanError("MARZBAN_URL is not set")
    return url


def token() -> str:
    ready = os.environ.get("MARZBAN_TOKEN", "").strip()
    if ready:
        return ready
    username = os.environ.get("MARZBAN_USERNAME", "").strip()
    password = os.environ.get("MARZBAN_PASSWORD", "")
    if not username or not password:
        raise MarzbanError("Set MARZBAN_TOKEN or MARZBAN_USERNAME + MARZBAN_PASSWORD")
    with httpx.Client(timeout=TIMEOUT) as client:
        response = client.post(f"{base_url()}/api/admin/token", data={"username": username, "password": password})
    if response.status_code >= 300:
        raise MarzbanError(f"Marzban login failed (HTTP {response.status_code})")
    access = response.json().get("access_token")
    if not access:
        raise MarzbanError("Marzban did not return access_token")
    return str(access)


def _headers(access: str | None = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {access or token()}", "Accept": "application/json"}


def get_user(username: str, headers: dict[str, str] | None = None) -> dict[str, Any] | None:
    with httpx.Client(timeout=TIMEOUT) as client:
        response = client.get(f"{base_url()}/api/user/{username}", headers=headers or _headers())
    if response.status_code == 404:
        return None
    if response.status_code >= 300:
        raise MarzbanError(f"Marzban GET user failed (HTTP {response.status_code})")
    return response.json()


def enabled_protocols(headers: dict[str, str] | None = None) -> list[str]:
    """Protocols that have at least one inbound; Marzban rejects users with any other protocol."""
    with httpx.Client(timeout=TIMEOUT) as client:
        response = client.get(f"{base_url()}/api/inbounds", headers=headers or _headers())
    if response.status_code >= 300:
        raise MarzbanError(f"Marzban GET inbounds failed (HTTP {response.status_code})")
    data = response.json()
    protocols = [name for name, inbounds in data.items() if inbounds] if isinstance(data, dict) else []
    if not protocols:
        raise MarzbanError("Marzban has no inbounds configured")
    return protocols


def provision(username: str, days: int, traffic_bytes: int) -> str:
    headers = _headers()
    now = int(time.time())
    extra = days * 86400
    existing = get_user(username, headers)
    if existing:
        current = int(existing.get("expire") or 0)
        start = current if current > now else now
        # Marzban compares data_limit with used_traffic, so the new quota goes on top of
        # whatever is used (or still left) instead of replacing it; 0 = unlimited.
        used = int(existing.get("used_traffic") or 0)
        quota_base = max(used, int(existing.get("data_limit") or 0))
        payload = {
            "expire": start + extra,
            "data_limit": quota_base + traffic_bytes if traffic_bytes > 0 else 0,
            "status": "active",
        }
        with httpx.Client(timeout=TIMEOUT) as client:
            response = client.put(f"{base_url()}/api/user/{username}", headers=headers, json=payload)
        if response.status_code >= 300:
            raise MarzbanError(f"Marzban update user failed (HTTP {response.status_code}): {response.text[:300]}")
        data = response.json()
    else:
        payload = {
            "username": username,
            "proxies": {protocol: {} for protocol in enabled_protocols(headers)},
            "expire": now + extra,
            "data_limit": traffic_bytes,
            "data_limit_reset_strategy": "no_reset",
            "status": "active",
        }
        with httpx.Client(timeout=TIMEOUT) as client:
            response = client.post(f"{base_url()}/api/user", headers=headers, json=payload)
        if response.status_code >= 300:
            raise MarzbanError(f"Marzban create user failed (HTTP {response.status_code}): {response.text[:300]}")
        data = response.json()
    return absolute_subscription_url(str(data.get("subscription_url") or ""))


def absolute_subscription_url(url: str) -> str:
    """Marzban returns /sub/<token> when XRAY_SUBSCRIPTION_URL_PREFIX is empty."""
    if not url.startswith("/"):
        return url
    prefix = (os.environ.get("MARZBAN_SUB_URL", "").strip() or base_url()).rstrip("/")
    return prefix + url
