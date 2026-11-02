"""Middleware sinh request_id và log JSON ra stdout."""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

request_id_var: ContextVar[str] = ContextVar("request_id", default="")
user_id_var: ContextVar[str] = ContextVar("user_id", default="")


def get_request_id() -> str:
    return request_id_var.get()


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
            "user_id": user_id_var.get(),
        }
        for extra in (
            "route",
            "step",
            "provider",
            "model",
            "status",
            "latency_ms",
            "fallback_reason",
        ):
            value = getattr(record, extra, None)
            if value is not None:
                payload[extra] = value
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").handlers = [handler]
    logging.getLogger("uvicorn.access").propagate = False


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Sinh request_id cho mọi request và gắn vào response header."""

    async def dispatch(self, request: Request, call_next):
        # Always server-generated: a client ID must not collide with quota leases/logs.
        rid = str(uuid.uuid4())
        token = request_id_var.set(rid)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except BaseException:
            request_id_var.reset(token)
            raise
        response.headers["X-Request-Id"] = rid
        elapsed = int((time.perf_counter() - started) * 1000)
        logging.getLogger("ai.access").info(
            "request",
            extra={
                "route": request.url.path,
                "status": response.status_code,
                "latency_ms": elapsed,
            },
        )
        request_id_var.reset(token)
        return response
