"""A tiny fake of the Telegram Bot API for E2E tests (never used by the real app).

Run: python tests/fake_telegram_server.py <port>

Behaviour of ``sendVideo`` is driven by the caption so tests can trigger each case:
* contains ``[rate-limit]`` → 429 with retry_after=1 every time
* contains ``[not-admin]``  → 403 "bot is not a member"
* anything else            → success with an increasing message_id
``GET /calls`` lists received calls; ``POST /reset`` clears them.
"""

from __future__ import annotations

import sys
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()
calls: list[dict[str, Any]] = []
counter = {"message_id": 100}


def _fail(status: int, description: str, **extra: Any) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error_code": status, "description": description, **extra}, status
    )


@app.post("/bot{token}/sendVideo")
async def send_video(token: str, request: Request) -> JSONResponse:
    form = await request.form()
    video = form.get("video")
    caption = str(form.get("caption") or "")
    calls.append(
        {
            "method": "sendVideo",
            "chat_id": form.get("chat_id"),
            "caption": caption,
            "filename": getattr(video, "filename", None),
        }
    )
    if "[rate-limit]" in caption:
        return _fail(429, "Too Many Requests: retry after 1", parameters={"retry_after": 1})
    if "[not-admin]" in caption:
        return _fail(403, "Forbidden: bot is not a member of the channel chat")
    counter["message_id"] += 1
    return JSONResponse({"ok": True, "result": {"message_id": counter["message_id"]}})


@app.get("/calls")
def list_calls() -> list[dict[str, Any]]:
    return calls


@app.post("/reset")
def reset() -> dict[str, bool]:
    calls.clear()
    return {"ok": True}


if __name__ == "__main__":  # pragma: no cover
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
