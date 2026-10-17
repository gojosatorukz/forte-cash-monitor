"""Authenticate before multipart parsing and bound streaming request bodies."""

import asyncio
import secrets
import time

from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
    (b"cache-control", b"no-store"),
    (b"x-robots-tag", b"noindex, nofollow, nosnippet"),
    (
        b"content-security-policy",
        (
            b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            b"img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; "
            b"object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        ),
    ),
]


class RequestGuard:
    def __init__(self, app, api_key: str, max_body_bytes: int, upload_timeout_sec: int):
        self.app = app
        self.key = api_key.encode()
        self.max_body = max_body_bytes
        self.upload_timeout = upload_timeout_sec

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def secure_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + SECURITY_HEADERS
            await send(message)

        headers = dict(scope["headers"])
        if scope["path"].startswith("/api/v1/"):
            key = headers.get(b"x-api-key", b"")
            if not secrets.compare_digest(key, self.key):
                return await JSONResponse(
                    {"detail": "Invalid or missing X-API-Key"}, status_code=401
                )(scope, receive, secure_send)
        guarded = scope["method"] == "POST" and scope["path"] == "/api/v1/analyze"
        if guarded:
            scope.setdefault("state", {})["upload_started_at"] = time.time()
            try:
                length = int(headers.get(b"content-length", b"0"))
            except ValueError:
                length = 0
            if length > self.max_body:
                return await JSONResponse(
                    {"detail": "Request exceeds the upload limit"}, status_code=413
                )(scope, receive, secure_send)
        total = 0
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.upload_timeout

        async def bounded_receive():
            nonlocal total
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise HTTPException(408, "Upload timed out")
            try:
                message = await asyncio.wait_for(receive(), timeout=remaining)
            except TimeoutError as exc:
                raise HTTPException(408, "Upload timed out") from exc
            total += len(message.get("body", b""))
            if total > self.max_body:
                raise HTTPException(413, "Request exceeds the upload limit")
            return message

        await self.app(scope, bounded_receive if guarded else receive, secure_send)
