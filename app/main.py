"""FastAPI app cho AI Service (hệ thống cho thuê trọ)."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.chat import router as chat_router
from app.api.events import router as events_router
from app.api.health import router as health_router
from app.api.recommendations import router as recommendations_router
from app.chat.llm.base import LLMProvider
from app.chat.llm.breaker import CircuitBreaker
from app.chat.llm.external_provider import ExternalProvider
from app.chat.llm.gateway import Gateway
from app.chat.llm.local_provider import LocalProvider
from app.chat.llm.rules_provider import RulesProvider
from app.config import get_settings
from app.core.deps import get_current_tenant
from app.embeddings.model import load_embedding_model
from app.embeddings.store import embedding_store
from app.observability.call_logger import CallLogger
from app.observability.request_context import (
    RequestContextMiddleware,
    configure_logging,
)

logger = logging.getLogger("ai")
_breakers = {name: CircuitBreaker() for name in ("local", "external")}


def build_gateway() -> Gateway:
    """Dựng chuỗi fallback Local -> External -> Rules."""
    settings = get_settings()
    providers: list[LLMProvider] = [
        LocalProvider(
            base_url=settings.local_llm_base_url,
            model=settings.local_llm_model,
            api_key=settings.local_llm_api_key,
            timeout=settings.local_timeout_seconds,
        )
    ]

    if settings.allow_external_fallback:
        if settings.external_provider.lower() == "anthropic":
            providers.append(
                ExternalProvider(
                    provider="anthropic",
                    model=settings.external_model,
                    anthropic_api_key=settings.anthropic_api_key,
                    timeout=settings.local_timeout_seconds,
                )
            )
        else:
            providers.append(
                ExternalProvider(
                    provider="gemini",
                    model=settings.external_model,
                    gemini_api_key=settings.gemini_api_key,
                    timeout=settings.local_timeout_seconds,
                )
            )

    providers.append(RulesProvider())

    breakers = _breakers
    call_logger = CallLogger()

    from app.core.rate_limit import usage_tracker

    return Gateway(
        providers,
        breakers,
        call_logger,
        timeout=settings.local_timeout_seconds,
        external_cap_check=usage_tracker.external_cap_reached_anywhere,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    logger.info("AI Service khởi động")

    settings = get_settings()
    try:
        embedding_store.load(settings.embedding_model)
    except Exception:
        logger.warning("Không nạp được vector embedding", exc_info=True)

    load_embedding_model()

    async def reload_vectors():
        while True:
            await asyncio.sleep(max(settings.embedding_reload_seconds, 1))
            try:
                await asyncio.to_thread(embedding_store.load, settings.embedding_model)
            except Exception:
                logger.warning("Vector reload failed; keeping last valid snapshot")

    reloader = asyncio.create_task(reload_vectors())
    try:
        yield
    finally:
        reloader.cancel()
        with suppress(asyncio.CancelledError):
            await reloader
    logger.info("AI Service dừng")


app = FastAPI(
    title="AI Service - Rental Management",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestContextMiddleware)

app.include_router(health_router)
app.include_router(recommendations_router)
app.include_router(chat_router)
app.include_router(events_router)


@app.get("/ai/me")
def me(user=Depends(get_current_tenant)):
    """Thông tin user hiện tại (dùng để kiểm tra JWT còn hiệu lực)."""
    return {
        "id": user.id,
        "scopes": list(user.scopes),
        "auth_version": user.auth_version,
    }
