"""Dependency dùng chung cho các router: singleton Gateway."""

from __future__ import annotations

from app.chat.llm.gateway import Gateway


def get_gateway() -> Gateway:
    # FastAPI caches this dependency only inside the current request.
    from app.main import build_gateway

    return build_gateway()
