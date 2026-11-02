import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import chat as chat_api
from app.api.deps import get_gateway
from app.chat.llm.gateway import CallContext, Gateway
from app.chat.llm.rules_provider import RulesProvider
from app.chat.schemas import ExtractResult, SearchFilters
from app.core.deps import get_current_tenant
from app.core.security import AuthenticatedUser
from app.db.models_ai import AiMessage, AiUsageDaily, LlmCallLog
from app.observability.call_logger import CallLogger


def test_chat_sse_persists_clarification_and_counts_turn(factory, monkeypatch):
    import app.core.rate_limit as limits

    for module in (chat_api, limits):
        monkeypatch.setattr(module, "ai_session_factory", factory)
    monkeypatch.setattr(chat_api, "core_session_factory", factory)
    app = FastAPI()
    app.include_router(chat_api.router)
    app.dependency_overrides[get_current_tenant] = lambda: AuthenticatedUser(
        "tenant", 0, ("ROLE_TENANT",)
    )
    app.dependency_overrides[get_gateway] = lambda: Gateway(
        [RulesProvider()], {}, CallLogger()
    )
    response = TestClient(app).post("/ai/chat", json={"message": "hello"})
    assert response.status_code == 200
    frames = [
        json.loads(frame.split("data: ", 1)[1])
        for frame in response.text.strip().split("\n\n")
    ]
    assert frames[-1]["status"] == "CLARIFY"
    assert frames[-1]["message_id"]
    with factory() as s:
        assistant = s.get(AiMessage, frames[-1]["message_id"])
        assert assistant.extract_provider == "rules"
        assert assistant.total_latency_ms is not None
        assert (
            s.scalar(
                select(AiUsageDaily).where(AiUsageDaily.user_id == "tenant")
            ).request_count
            == 1
        )
        assert len(list(s.scalars(select(LlmCallLog)))) == 1


@pytest.mark.asyncio
async def test_provider_payload_sanitized_and_logs_record_end_of_stream():
    class External:
        name = "external"
        model = "test"
        last_usage = None

        async def extract(self, text, previous):
            assert "person@example.com" not in text
            return ExtractResult(
                intent="SEARCH", filters=SearchFilters(price_max=3000000)
            )

        async def compose(self, text, listings):
            assert "person@example.com" not in text
            assert "person@example.com" not in listings[0]["title"]
            yield "one"
            yield "two"

    logger = CallLogger()
    gateway = Gateway([External()], {}, logger)
    ctx = CallContext("request", "user", "EXTRACT")
    await gateway.extract(ctx, "person@example.com", None)
    chunks = gateway.compose_stream(
        CallContext("request", "user", "COMPOSE"),
        "person@example.com",
        [{"title": "person@example.com"}],
    )
    assert await anext(chunks) == ("external", "one")
    assert len(logger.records) == 1
    assert [chunk async for _, chunk in chunks] == ["two"]
    assert len(logger.records) == 2
    assert all(r.status == "SUCCESS" for r in logger.records)


def test_gateway_state_is_request_scoped():
    first, second = get_gateway(), get_gateway()
    assert first is not second
    assert first.call_logger is not second.call_logger
    assert first.chain[0] is not second.chain[0]
    assert first.breakers is second.breakers
