"""Signing in with Google.

Amit, 17 Sep 2026: *"saath hi login with Google ka bhi option chahiye."*

Ported from manuscript-ngine's `apps/accounts/google_oauth.py` — same estate, same
owner, and the same Google client, so the reasoning that was worked out there is not
re-derived here. What differs is what happens **after** Google answers, and that
difference is the whole of `apps/identity/google_login.py`.

**No domain allowlist.** This registry's 13,412 members are academics at universities
across the world on gmail, an institute address, or whatever their department gave them.
A domain list would lock out almost everybody it serves. The setting exists for the day
somebody wants it and is empty.

**Why the id_token's signature is not verified here.** It did not come from the browser.
This server fetched it over TLS from Google's own token endpoint in exchange for our
client secret. Signature verification protects a token that passed through somebody
else's hands; this one never did. The load-bearing check is the state/nonce pair below,
which is what stops an attacker feeding us a code from a different session.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.parse
import urllib.request

from django.conf import settings as django_settings

AUTHORISE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"

#: Long enough to pick an account and type a password, short enough that a state value
#: copied out of a browser history is worthless.
STATE_TTL_SECONDS = 10 * 60

NONCE_COOKIE = "gauth_nonce"


# --------------------------------------------------------------------- configuration

def client_id() -> str:
    return os.environ.get("APID_GOOGLE_CLIENT_ID", "").strip()


def client_secret() -> str:
    return os.environ.get("APID_GOOGLE_CLIENT_SECRET", "").strip()


def allowed_domains() -> list:
    raw = os.environ.get("APID_GOOGLE_ALLOWED_DOMAINS", "")
    return [d.strip().lower() for d in raw.replace(";", ",").split(",") if d.strip()]


def may_create_accounts() -> bool:
    """Off. A Google account proves an address, not membership of this registry.

    Until registration exists here, signing in with Google means *claiming an account
    that is already in the registry*. Turning this on without a registration flow would
    make an account with no APID, no profile and no editorial history — which looks like
    a successful sign-in and is the start of a duplicate person.
    """
    return os.environ.get("APID_GOOGLE_CREATE_ACCOUNTS", "") == "1"


def configured() -> bool:
    """True only when a sign-in attempt could actually succeed.

    The login page asks this before drawing the button. A button that appears and then
    fails is worse than no button: the person has already decided to trust it.
    """
    return bool(client_id() and client_secret())


def domain_allowed(email: str) -> bool:
    domains = allowed_domains()
    if not domains:
        return True
    address = (email or "").strip().lower()
    return "@" in address and address.rsplit("@", 1)[1] in domains


def redirect_uri(request) -> str:
    """Built from the request rather than configured, so it cannot drift from the host
    somebody is actually on. This is the URI to register in Google Cloud Console:
    `https://apid.celnet.in/accounts/google/callback/`."""
    return request.build_absolute_uri("/accounts/google/callback/")


# --------------------------------------------------------------------------- the flow

def _sign(payload: str) -> str:
    digest = hmac.new(django_settings.SECRET_KEY.encode(), payload.encode(),
                      hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def make_state(nonce: str, next_path: str = "") -> str:
    """A signed, expiring state tied to a nonce this browser holds in a cookie.

    Signed rather than stored: the callback is a separate request that may land on a
    different worker, and server-side state would mean a shared store for something
    that has to survive exactly ten minutes.
    """
    body = json.dumps({"n": nonce, "t": int(time.time()), "p": next_path},
                      separators=(",", ":"))
    packed = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    return f"{packed}.{_sign(packed)}"


def read_state(value: str, nonce: str) -> dict | None:
    """The state's contents, or None if it is forged, expired, or not this browser's."""
    try:
        packed, signature = (value or "").split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(_sign(packed), signature):
        return None
    try:
        padded = packed + "=" * (-len(packed) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, TypeError):
        return None
    if int(time.time()) - int(data.get("t", 0)) > STATE_TTL_SECONDS:
        return None
    # The cookie half. Without it our own signed state would work in anybody's browser,
    # which is what makes a login-CSRF possible.
    if not nonce or not hmac.compare_digest(str(data.get("n", "")), nonce):
        return None
    return data


def new_nonce() -> str:
    return secrets.token_urlsafe(24)


def authorise_url(uri: str, state: str) -> str:
    return AUTHORISE_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id(),
        "redirect_uri": uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        # Always ask which account. Academics have a personal and an institutional
        # Google account, and here the address *is* the identity.
        "prompt": "select_account",
    })


def claims(code: str, uri: str) -> dict:
    """Exchange the code for the id_token's claims. Raises OSError/ValueError."""
    body = urllib.parse.urlencode({
        "code": code, "client_id": client_id(), "client_secret": client_secret(),
        "redirect_uri": uri, "grant_type": "authorization_code",
    }).encode()
    request = urllib.request.Request(
        TOKEN_URL, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode())

    token = payload.get("id_token") or ""
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("no id_token in Google's answer")
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))
