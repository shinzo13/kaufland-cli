from __future__ import annotations

import urllib.parse
from typing import Any

from . import auth, http
from .config import (
    API_BASE,
    APP_VERSION,
    LOYALTY_CLIENT_ID,
    MAX_PAGE_SIZE,
    KauflandError,
    country,
)


def _page_url(user_id: str, start: int, limit: int) -> str:
    query = urllib.parse.urlencode(
        {
            "start": start,
            "limit": limit,
            "country": country(),
            "version": 2,
        }
    )
    return f"{API_BASE}/api/v2/customers/{urllib.parse.quote(user_id, safe='')}/transactions?{query}"


def _fetch_page(token: str, user_id: str, start: int, limit: int) -> Any:
    url = _page_url(user_id, start, limit)
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "client-id": LOYALTY_CLIENT_ID,
        "app-platform": "Android",
        "app-version": APP_VERSION,
    }
    status, payload = http.request_json("GET", url, headers=headers)
    if status == 401:
        token, _ = auth.access_token(force_refresh=True)
        headers["Authorization"] = f"Bearer {token}"
        status, payload = http.request_json("GET", url, headers=headers)
    if status != 200:
        raise KauflandError(f"transactions endpoint returned HTTP {status}: {payload}")
    return payload


def _extract(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("transactions", "items", "receipts", "results", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                return _extract(value)
    raise KauflandError(f"unexpected response shape: {payload!r:.300}")


def fetch(limit: int = 20, *, fetch_all: bool = False) -> list[dict]:
    token, user_id = auth.access_token()
    page_size = MAX_PAGE_SIZE if fetch_all else min(limit, MAX_PAGE_SIZE)
    collected: list[dict] = []
    start = 0
    while True:
        batch = _extract(_fetch_page(token, user_id, start, page_size))
        collected.extend(batch)
        if len(batch) < page_size:
            break
        if not fetch_all and len(collected) >= limit:
            break
        start += len(batch)
    return collected if fetch_all else collected[:limit]


def _minor(value: Any) -> float:
    if isinstance(value, str):
        value = value.replace(",", ".")
    try:
        return float(value) / 100.0
    except (TypeError, ValueError):
        return 0.0


def line_items(receipts: list[dict]) -> list[dict]:
    rows = []
    for receipt in receipts:
        store = receipt.get("store") if isinstance(receipt.get("store"), dict) else {}
        for refund, key in ((False, "positions"), (True, "refundPositions")):
            for position in receipt.get(key) or []:
                if not isinstance(position, dict):
                    continue
                sign = -1 if refund else 1
                rows.append(
                    {
                        "receipt_id": receipt.get("id"),
                        "timestamp": receipt.get("timestamp"),
                        "store": store.get("name"),
                        "city": store.get("city"),
                        "name": position.get("name"),
                        "quantity": position.get("quantity", 1),
                        "unit": position.get("quantityUnit"),
                        "unit_price": round(_minor(position.get("unitPrice")) * sign, 2),
                        "total": round(_minor(position.get("total")) * sign, 2),
                        "currency": receipt.get("currency"),
                        "gtin": position.get("gtin"),
                        "refund": refund,
                    }
                )
    return rows


def summaries(receipts: list[dict]) -> list[dict]:
    rows = []
    for receipt in receipts:
        store = receipt.get("store") if isinstance(receipt.get("store"), dict) else {}
        rows.append(
            {
                "receipt_id": receipt.get("id"),
                "timestamp": receipt.get("timestamp"),
                "store": store.get("name"),
                "city": store.get("city"),
                "total": round(_minor(receipt.get("sum")), 2),
                "saving": round(_minor(receipt.get("saving")), 2),
                "currency": receipt.get("currency"),
                "items": len(receipt.get("positions") or []),
                "bonus_points": receipt.get("bonusPoints"),
            }
        )
    return rows
