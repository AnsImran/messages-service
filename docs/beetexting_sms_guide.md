# BEEtexting SMS Sending — Complete Reference Guide

> Extracted from `ring_central_zoho_desk_bridge`. This guide covers **only** the SMS sending flow — everything you need to send an outbound SMS via the BEEtexting API.

---

## 1. Required Environment Variables

Only **3 secrets** are needed to send SMS:

| Variable                   | Purpose                                      |
|----------------------------|----------------------------------------------|
| `BEETEXTING_CLIENT_ID`     | OAuth2 client ID (M2M client credentials)    |
| `BEETEXTING_CLIENT_SECRET` | OAuth2 client secret (M2M client credentials)|
| `BEETEXTING_API_KEY`       | API key sent as `x-api-key` header           |

> **Values are stored in `.env` — see [.env.example](../.env.example) for the template. Never commit real credentials.**

---

## 2. Authentication — Get a Bearer Token

Sending an SMS requires a Bearer token obtained via the **OAuth2 Client Credentials** flow. A fresh token is requested before each API call (no caching in the original implementation). Tokens are valid for ~3600 seconds (1 hour).

### Request

```
POST https://auth.beetexting.com/oauth2/token/
```

**Headers:**

| Header          | Value                                          |
|-----------------|------------------------------------------------|
| `Authorization` | `Basic <base64(CLIENT_ID:CLIENT_SECRET)>`      |
| `Content-Type`  | `application/x-www-form-urlencoded`            |

**Body (form-encoded):**

```
grant_type=client_credentials&scope=https://com.beetexting.scopes/ReadContact%20https://com.beetexting.scopes/WriteContact%20https://com.beetexting.scopes/SendMessage
```

The three scopes broken out:
- `https://com.beetexting.scopes/ReadContact`
- `https://com.beetexting.scopes/WriteContact`
- `https://com.beetexting.scopes/SendMessage`

### Response

```json
{
  "access_token": "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "Bearer",
  "expires_in": 3600
}
```

Extract `access_token` from the response — this is the Bearer token used in Step 3.

---

## 3. Send an SMS

### Request

```
POST https://connect.beetexting.com/prod/message/sendsms
```

**Headers:**

| Header          | Value                            |
|-----------------|----------------------------------|
| `Authorization` | `Bearer <access_token>`          |
| `x-api-key`     | `<BEETEXTING_API_KEY>`           |

**Query Parameters** (passed as URL params, **NOT** in the body):

| Param  | Description                            | Example           |
|--------|----------------------------------------|--------------------|
| `from` | Sender phone number (E.164 format)     | `+1XXXXXXXXXX`     |
| `to`   | Recipient phone number (E.164 format)  | `+1XXXXXXXXXX`     |
| `text` | Message body                           | `Hello from PACS!` |

**Body:** Empty — all data goes in query params.

### Response

JSON object containing the sent message details (message ID, status, etc.).

### Constructed URL Example

```
POST https://connect.beetexting.com/prod/message/sendsms?from=%2B1XXXXXXXXXX&to=%2B1XXXXXXXXXX&text=Hello+from+PACS!
```

---

## 4. Minimal Python Example

Copy-paste-ready. Requires only `requests` and `os` (plus `base64` from stdlib).

```python
import base64
import os

import requests


# ── Auth ────────────────────────────────────────────────────────────────────

TOKEN_URL = "https://auth.beetexting.com/oauth2/token/"
SCOPES = (
    "https://com.beetexting.scopes/ReadContact "
    "https://com.beetexting.scopes/WriteContact "
    "https://com.beetexting.scopes/SendMessage"
)


def get_access_token() -> str:
    """Fetch a fresh Bearer token via OAuth2 client_credentials."""
    client_id = os.environ["BEETEXTING_CLIENT_ID"]
    client_secret = os.environ["BEETEXTING_CLIENT_SECRET"]

    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()

    response = requests.post(
        TOKEN_URL,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "client_credentials",
            "scope": SCOPES,
        },
    )
    response.raise_for_status()
    return response.json()["access_token"]


# ── Send SMS ────────────────────────────────────────────────────────────────

SEND_SMS_URL = "https://connect.beetexting.com/prod/message/sendsms"


def send_sms(from_number: str, to_number: str, text: str) -> dict:
    """Send an SMS via BEEtexting and return the API response."""
    api_key = os.environ["BEETEXTING_API_KEY"]
    token = get_access_token()

    response = requests.post(
        SEND_SMS_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "x-api-key": api_key,
        },
        params={
            "from": from_number,
            "to": to_number,
            "text": text,
        },
    )
    response.raise_for_status()
    return response.json()


# ── Usage ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    result = send_sms(
        from_number=os.environ["BEETEXTING_FROM_NUMBER"],
        to_number="+1XXXXXXXXXX",
        text="Hello from the microservice!",
    )
    print(result)
```

---

## 5. cURL Equivalents

### Get Token

```bash
curl -X POST https://auth.beetexting.com/oauth2/token/ \
  -H "Authorization: Basic $(echo -n '$BEETEXTING_CLIENT_ID:$BEETEXTING_CLIENT_SECRET' | base64)" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials&scope=https://com.beetexting.scopes/ReadContact%20https://com.beetexting.scopes/WriteContact%20https://com.beetexting.scopes/SendMessage"
```

### Send SMS

```bash
curl -X POST "https://connect.beetexting.com/prod/message/sendsms?from=%2B1XXXXXXXXXX&to=%2B1XXXXXXXXXX&text=Hello" \
  -H "Authorization: Bearer <ACCESS_TOKEN>" \
  -H "x-api-key: $BEETEXTING_API_KEY"
```

---

## 6. API Endpoints Reference

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `https://auth.beetexting.com/oauth2/token/` | POST | Get Bearer token (client_credentials) |
| `https://connect.beetexting.com/prod/message/sendsms` | POST | Send an SMS |
| `https://connect.beetexting.com/prod/message/getmessagebyid/{id}` | GET | Fetch a message by ID |

---

## 7. Important Notes

- **Dual auth:** Every API call requires **both** a Bearer token (`Authorization` header) and an API key (`x-api-key` header). Missing either will fail.
- **Phone format:** Numbers must be in E.164 format (e.g., `+1XXXXXXXXXX`). No dashes, spaces, or parentheses.
- **No caching:** The original implementation fetches a fresh token before every single API call. For higher throughput, you could cache the token and reuse it for up to ~55 minutes (leaving a 5-minute buffer before the 3600s expiry).
- **Empty POST body:** The SMS endpoint is a POST but all parameters go in the query string, not the request body. This is a quirk of the BEEtexting API.
- **Error handling:** `response.raise_for_status()` will throw `requests.exceptions.HTTPError` on 4xx/5xx responses.

---

## 8. What You DON'T Need for Sending SMS

The original `ring_central_zoho_desk_bridge` repo also has a **user-level OAuth2 authorization-code flow** with its own set of credentials:

| Variable                        | NOT needed for SMS sending |
|---------------------------------|----------------------------|
| `BEETEXTING_USER_CLIENT_ID`     | Only for webhook subscriptions |
| `BEETEXTING_USER_CLIENT_SECRET` | Only for webhook subscriptions |
| `BEETEXTING_USER_API_KEY`       | Only for webhook subscriptions |
| `BEETEXTING_REFRESH_TOKEN`      | Only for webhook subscriptions |
| `BEETEXTING_ORG_ID`             | Only for webhook subscriptions |
| `BEETEXTING_DEPT_ID`            | Only for webhook subscriptions |

That flow uses `grant_type=authorization_code` with a browser login, refresh tokens stored in `beetexting_refresh_token.txt`, and is exclusively for managing webhook subscriptions (subscribing to inbound messages). **None of it is needed to send an SMS.**

---

## Source Files (original implementation)

| File | What it does |
|------|-------------|
| `ring_central_zoho_desk_bridge/src/beetexting_auth.py` | `get_access_token()` — M2M token via client_credentials |
| `ring_central_zoho_desk_bridge/src/beetexting_send_sms.py` | `send_sms()` — POST to sendsms endpoint |
| `ring_central_zoho_desk_bridge/.env` | All credential values |
