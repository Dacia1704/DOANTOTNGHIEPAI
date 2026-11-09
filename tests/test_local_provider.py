import json

import httpx
import pytest

from app.chat.llm.local_provider import LocalProvider


@pytest.mark.asyncio
async def test_qwen_extract_uses_non_thinking_and_retries_invalid_json(monkeypatch):
    provider = LocalProvider("http://localhost:11434/v1", "qwen3:4b")
    calls = []

    async def post(path, payload):
        calls.append(payload)
        content = "not json" if len(calls) == 1 else json.dumps({
            "intent": "SEARCH", "filters": {"price_max": 3000000, "occupants": 2}
        })
        return {"choices": [{"message": {"content": content}}]}

    monkeypatch.setattr(provider, "_post", post)
    result = await provider.extract("phong duoi 3tr cho 2 nguoi", None)
    assert result.filters.price_max == 3000000
    assert result.filters.occupants == 2
    assert len(calls) == 2
    assert all(call["reasoning_effort"] == "none" for call in calls)
    assert all(call["max_tokens"] == 512 for call in calls)
    assert calls[0]["messages"][-1]["content"].endswith("/no_think")
    assert "reasoning_effort" not in LocalProvider(
        "http://localhost/v1", "another-model"
    )._generation_options(max_tokens=512)
    instruct = LocalProvider("http://localhost/v1", "qwen3:4b-instruct-2507-q4_K_M")
    assert "reasoning_effort" not in instruct._generation_options(max_tokens=512)
    messages = [{"role": "user", "content": "find rooms"}]
    assert instruct._without_thinking(messages) == messages


@pytest.mark.asyncio
async def test_compose_stream_limits_prompt_but_preserves_total_and_usage(monkeypatch):
    provider = LocalProvider("http://localhost:11434/v1", "qwen3:4b")
    cards = [dict(title=f"room-{i}", amenities=[]) for i in range(20)]

    def respond(request):
        payload = json.loads(request.content)
        assert payload["reasoning_effort"] == "none"
        assert payload["max_tokens"] == 384
        assert payload["messages"][-1]["content"].endswith("/no_think")
        prompt = payload["messages"][-1]["content"]
        assert "20" in prompt and "room-4" in prompt and "room-5" not in prompt
        chunks = [
            {"choices": [{"delta": {"reasoning": "hidden"}}]},
            {"choices": [{"delta": {"content": "Found rooms"}}]},
            {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 5}},
        ]
        body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
        return httpx.Response(200, text=body + "data: [DONE]\n\n")

    client_type = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client_type(
        **kwargs, transport=httpx.MockTransport(respond)
    ))
    assert [part async for part in provider.compose("find rooms", cards)] == ["Found rooms"]
    assert provider.last_usage.output_tokens == 5
    assert len(cards) == 20


@pytest.mark.asyncio
async def test_empty_new_search_asks_for_details_without_changing_followup(monkeypatch):
    provider = LocalProvider("http://localhost/v1", "qwen3:4b-instruct-2507-q4_K_M")

    async def post(path, payload):
        return {"choices": [{"message": {"content": json.dumps({
            "intent": "SEARCH", "filters": {}
        })}}]}

    monkeypatch.setattr(provider, "_post", post)
    result = await provider.extract("find rooms", None)
    assert result.intent == "CLARIFY"
    assert result.clarification_question
    result = await provider.extract("more rooms", {"price_max": 3000000})
    assert result.intent == "SEARCH"
