"""GET /ai/health - trạng thái DB, embedding, LLM local/ngoài, breaker."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Response
from sqlalchemy import text

from app.api.deps import get_gateway
from app.chat.llm.gateway import Gateway
from app.config import get_settings
from app.db.session import ai_engine, core_engine
from app.embeddings.store import embedding_store

logger = logging.getLogger("ai.health")
router = APIRouter(prefix="/ai", tags=["ai"])


def _check_db(engine, label: str) -> dict:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            if label == "core DB":
                from app.db.inventory import inventory_query

                connection.execute(inventory_query().limit(0))
            else:
                connection.execute(
                    text("SELECT request_id FROM ai_chat_leases LIMIT 0")
                )
                connection.execute(text("SELECT user_id FROM ai_user_locks LIMIT 0"))
                connection.execute(
                    text("SELECT listing_ids FROM recommendation_requests LIMIT 0")
                )
        return {"status": "UP"}
    except Exception as exc:
        logger.warning("%s khong ket noi duoc: %s", label, exc)
        return {"status": "DOWN", "error": type(exc).__name__}


@router.get("/health")
def health(response: Response, gateway: Gateway = Depends(get_gateway)) -> dict:
    settings = get_settings()
    breakers = {
        name: {
            "open": breaker.opened_at is not None,
            "failures": len(breaker.failures),
        }
        for name, breaker in gateway.breakers.items()
    }
    databases = {
        "core": _check_db(core_engine, "core DB"),
        "ai": _check_db(ai_engine, "ai DB"),
    }
    ready = all(db["status"] == "UP" for db in databases.values())
    response.status_code = 200 if ready else 503
    return {
        "status": "UP" if ready else "DOWN",
        "db": databases,
        "embedding": {
            "model": settings.embedding_model,
            "loaded_vectors": len(embedding_store),
        },
        "llm": {
            "local": {
                "model": settings.local_llm_model,
                "base_url": settings.local_llm_base_url,
            },
            "external": {
                "provider": settings.external_provider,
                "model": settings.external_model,
                "enabled": settings.allow_external_fallback,
            },
        },
        "breakers": breakers,
    }
