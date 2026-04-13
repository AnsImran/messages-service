"""
Snapshot — microservice_sms, bare-minimum first iteration.

Captured 2026-04-13, immediately after the first end-to-end test SMS was
delivered successfully to Marko from the main company number. This is the
"version 0" of the service, kept as a single read-only reference file
before the production-grade refactor (API versioning, pydantic-settings,
typed schemas, app factory, lifespan, structured logging, error handlers).

Nothing in this file is imported anywhere — it exists purely so we can
diff the production layout against the original three-file implementation
and remember how small the working core actually was.

The original layout was:

    src/
    ├── main.py
    ├── schemas/
    │   ├── __init__.py        (empty)
    │   └── sms.py
    └── services/
        ├── __init__.py        (empty)
        └── beetexting.py

Run command at the time:
    uv run uvicorn main:app --app-dir src --port 8200

Test that proved it worked end-to-end:
    POST http://localhost:8200/send
        {"from_number":"+19494248180",
         "to_number":"+19493137724",
         "text":"Hi Marko — automated dev test, please ignore."}
    -> 200 {"ok": true, "provider_response": {"result": "Message Processed Successfully"}}
"""

# ============================================================================
# === src/main.py
# ============================================================================
from fastapi import FastAPI

from schemas.sms import SendSmsRequest
from services.beetexting import send_sms

app = FastAPI(title="microservice_sms")


@app.post("/send")
async def send(request: SendSmsRequest):
    provider_response = await send_sms(
        from_number=request.from_number,
        to_number=request.to_number,
        text=request.text,
    )
    return {"ok": True, "provider_response": provider_response}


# ============================================================================
# === src/schemas/sms.py
# ============================================================================
from pydantic import BaseModel  # noqa: E402


class SendSmsRequest(BaseModel):  # noqa: F811
    from_number: str
    to_number: str
    text: str


# ============================================================================
# === src/services/beetexting.py
# ============================================================================
import os  # noqa: E402

import httpx  # noqa: E402

TOKEN_SERVICE_URL = os.getenv("TOKEN_SERVICE_URL", "http://localhost:8100/api/v1/token")
BEETEXTING_SEND_URL = "https://connect.beetexting.com/prod/message/sendsms"


async def _get_credentials() -> tuple[str, str]:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(TOKEN_SERVICE_URL)
        response.raise_for_status()
        data = response.json()
        return data["access_token"], data["api_key"]


async def send_sms(from_number: str, to_number: str, text: str) -> dict:  # noqa: F811
    access_token, api_key = await _get_credentials()
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            BEETEXTING_SEND_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "x-api-key": api_key,
            },
            params={"from": from_number, "to": to_number, "text": text},
        )
        response.raise_for_status()
        return response.json()
