from __future__ import annotations

import base64
import getpass
import hashlib
import json
import os
import secrets
import time
import urllib.parse
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from . import http
from .config import (
    AUTHZ_PATH,
    BROWSER_UA,
    CALLBACK_SCHEME,
    CIDAAS_BASE,
    CIDAAS_CLIENT_ID,
    LOGIN_PATH,
    LOGIN_ROUTE,
    OAUTH_VERSION,
    REDIRECT_URI,
    TOKEN_PATH,
    USERINFO_URL,
    KauflandError,
    data_dir,
    token_path,
    ui_locales,
)

MAX_REDIRECT_HOPS = 10


def _write_tokens(tokens: dict[str, Any]) -> None:
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(tokens, indent=2))
    os.chmod(temp, 0o600)
    temp.replace(path)


def load_tokens() -> dict[str, Any]:
    path = token_path()
    if not path.exists():
        raise KauflandError("no saved login; run: kaufland login")
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise KauflandError(f"could not read {path}") from exc


def logout() -> Path | None:
    path = token_path()
    if not path.exists():
        return None
    path.unlink()
    return path


def _store(
    payload: Mapping[str, Any],
    *,
    previous_refresh: str | None = None,
    user_id: str | None = None,
) -> dict:
    tokens = {
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token") or previous_refresh,
        "expires_at": time.time() + float(payload.get("expires_in", 3600)),
        "user_id": user_id or (load_tokens().get("user_id") if token_path().exists() else None),
    }
    _write_tokens(tokens)
    return tokens


def _token_request(form: Mapping[str, str]) -> dict:
    status, payload = http.request_json(
        "POST",
        CIDAAS_BASE + TOKEN_PATH,
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
        body=urllib.parse.urlencode(form).encode(),
    )
    if status != 200 or not isinstance(payload, dict) or "access_token" not in payload:
        raise KauflandError(f"token endpoint returned HTTP {status}: {payload}")
    return payload


def access_token(*, force_refresh: bool = False) -> tuple[str, str]:
    tokens = load_tokens()
    if not tokens.get("user_id"):
        raise KauflandError("saved login has no user id; run: kaufland login")
    if not force_refresh and tokens.get("expires_at", 0) > time.time() + 60:
        return tokens["access_token"], tokens["user_id"]
    refresh = tokens.get("refresh_token")
    if not refresh:
        raise KauflandError("access token expired and no refresh token; run: kaufland login")
    payload = _token_request(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": CIDAAS_CLIENT_ID,
            "v": OAUTH_VERSION,
        }
    )
    tokens = _store(payload, previous_refresh=refresh)
    return tokens["access_token"], tokens["user_id"]


def _pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def _authorize(challenge: str, state: str) -> tuple[str, str]:
    url = (
        CIDAAS_BASE
        + AUTHZ_PATH
        + "?"
        + urllib.parse.urlencode(
            {
                "client_id": CIDAAS_CLIENT_ID,
                "response_type": "code",
                "redirect_uri": REDIRECT_URI,
                "ui_locales": ui_locales(),
                "v": OAUTH_VERSION,
                "view_type": "login",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
            }
        )
    )
    status, _, headers = http.request("GET", url, headers={"User-Agent": BROWSER_UA})
    location = http.header(headers, "Location") or ""
    if status != 302 or "requestId=" not in location:
        raise KauflandError(f"authorize endpoint returned HTTP {status}; expected a login redirect")
    request_id = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)["requestId"][0]
    return url, request_id


def _login_page_url(request_id: str) -> str:
    return (
        CIDAAS_BASE
        + LOGIN_PATH
        + "?"
        + urllib.parse.urlencode(
            {
                "requestId": request_id,
                "ui_locales": ui_locales(),
                "v": OAUTH_VERSION,
                "view_type": "login",
                "username_type": "email",
            }
        )
    )


def _post_credentials(page_url: str, form: Mapping[str, str]) -> tuple[dict | None, str | None]:
    status, raw, headers = http.request(
        "POST",
        page_url + "&_data=" + urllib.parse.quote(LOGIN_ROUTE, safe=""),
        headers={
            "User-Agent": BROWSER_UA,
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": CIDAAS_BASE,
            "Referer": page_url,
        },
        body=urllib.parse.urlencode(form).encode(),
    )
    redirect = http.header(headers, "X-Remix-Redirect") or http.header(headers, "Location")
    if redirect:
        return None, urllib.parse.urljoin(CIDAAS_BASE, redirect)
    payload = http.decode_json(raw)
    if status not in (200, 204) or not isinstance(payload, dict):
        raise KauflandError(f"login route returned HTTP {status} with no redirect: {raw[:300]!r}")
    return payload, None


def _follow_to_callback(url: str) -> str | None:
    for _ in range(MAX_REDIRECT_HOPS):
        if url.startswith(CALLBACK_SCHEME):
            return url
        _, _, headers = http.request("GET", url, headers={"User-Agent": BROWSER_UA})
        location = http.header(headers, "Location") or http.header(headers, "X-Remix-Redirect")
        if not location:
            return None
        url = urllib.parse.urljoin(url, location)
    return None


def _authorization_code(authorize_url: str, state: str, post_login_redirect: str | None) -> str:
    callback = _follow_to_callback(post_login_redirect) if post_login_redirect else None
    if not callback:
        callback = _follow_to_callback(authorize_url)
    if not callback:
        raise KauflandError(
            "login succeeded but no callback redirect was issued; "
            f"last tried: {post_login_redirect or authorize_url}"
        )
    params = urllib.parse.parse_qs(urllib.parse.urlparse(callback).query)
    if "error" in params:
        raise KauflandError(f"authorization failed: {params['error'][0]}")
    code = (params.get("code") or [None])[0]
    if not code:
        raise KauflandError(f"callback had no code: {callback}")
    returned_state = (params.get("state") or [None])[0]
    if returned_state and returned_state != state:
        raise KauflandError("state mismatch; run login again")
    return code


def _userinfo(token: str) -> dict:
    status, info = http.request_json(
        "GET",
        USERINFO_URL,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    if status != 200 or not isinstance(info, dict) or not info.get("sub"):
        raise KauflandError(f"userinfo returned HTTP {status}: {info}")
    return info


def login(email: str | None = None, password: str | None = None) -> dict:
    email = email or os.getenv("KAUFLAND_EMAIL") or input("Kaufland email: ").strip()
    password = password or os.getenv("KAUFLAND_PASSWORD") or getpass.getpass("Password: ")

    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(16)
    authorize_url, request_id = _authorize(challenge, state)
    page_url = _login_page_url(request_id)
    http.request("GET", page_url, headers={"User-Agent": BROWSER_UA})

    form = {
        "username": email,
        "password": password,
        "requestId": request_id,
        "usernameType": "email",
        "verificationType": "",
        "exchangeId": "",
        "resetRequestId": "",
        "action": "login",
        "code": "",
    }
    result, redirect = _post_credentials(page_url, form)
    if result is not None and not result.get("success"):
        detail = result.get("error") or json.dumps(result, ensure_ascii=False)
        raise KauflandError(f"login rejected: {detail}")
    result = result or {}

    needs_otp = result.get("otpFields") or result.get("exchangeId") or result.get("verificationType")
    if redirect is None and needs_otp:
        values = result.get("values") or {}
        form.update(
            {
                "code": input("One-time code: ").strip(),
                "action": values.get("action", "verify"),
                "exchangeId": result.get("exchangeId") or values.get("exchangeId", ""),
                "verificationType": result.get("verificationType") or values.get("verificationType", ""),
            }
        )
        result, redirect = _post_credentials(page_url, form)
        if result is not None and not result.get("success"):
            raise KauflandError(f"verification rejected: {result.get('error') or result}")

    code = _authorization_code(authorize_url, state, redirect)
    payload = _token_request(
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": CIDAAS_CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
            "v": OAUTH_VERSION,
        }
    )
    info = _userinfo(payload["access_token"])
    _store(payload, user_id=str(info["sub"]))
    return info


def status() -> dict:
    tokens = load_tokens()
    expires_at = tokens.get("expires_at", 0)
    return {
        "data_dir": str(data_dir()),
        "user_id": tokens.get("user_id"),
        "access_token_valid": expires_at > time.time(),
        "expires_in_seconds": max(0, int(expires_at - time.time())),
        "has_refresh_token": bool(tokens.get("refresh_token")),
    }
