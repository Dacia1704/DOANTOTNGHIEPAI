"""Kiểm thử cấu hình tắt AI ngoài (Gemini), bảo mật, PII, validation, circuit breaker & rate limit."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import jwt
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.chat import ChatRequest
from app.chat.llm.base import InvalidJsonError, InvalidValueError, ProviderError
from app.chat.llm.breaker import CircuitBreaker
from app.chat.llm.external_provider import ExternalProvider
from app.chat.llm.gateway import AllProvidersFailed, CallContext, Gateway
from app.chat.llm.local_provider import LocalProvider
from app.chat.llm.rules_provider import RulesProvider
from app.chat.sanitize import sanitize
from app.chat.schemas import ExtractResult, SearchFilters
from app.chat.service import get_or_create_conversation, validate_extracted
from app.config import Settings, get_settings
from app.core.rate_limit import UsageTracker
from app.core.security import AuthError, JwtVerifier
from app.db.models_ai import AiChatLease, AiConversation
from app.db.models_core import CoreUser
from app.main import build_gateway
from app.observability.call_logger import CallLogger
from tests.conftest import create_inmemory_dbs, make_provider_stub


# =============================================================================
# 1. ĐẢM BẢO TẮT HOÀN TOÀN GEMINI / EXTERNAL FALLBACK
# =============================================================================


def test_env_disables_external_fallback_and_gateway_contains_only_local_and_rules():
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.allow_external_fallback is False

    gateway = build_gateway()
    provider_names = [p.name for p in gateway.chain]
    assert provider_names == ["local", "rules"]
    assert isinstance(gateway.chain[0], LocalProvider)
    assert isinstance(gateway.chain[1], RulesProvider)
    assert not any(isinstance(p, ExternalProvider) for p in gateway.chain)


@pytest.mark.asyncio
async def test_external_http_guard_blocks_gemini_and_anthropic_urls():
    """Tầng mạng test phải chặn cứng mọi lời gọi ra Gemini hoặc Anthropic."""
    ext_gemini = ExternalProvider(provider="gemini", model="gemini-3.1-flash-lite", gemini_api_key="dummy")
    with pytest.raises(RuntimeError, match="BLOCKED external LLM"):
        await ext_gemini.extract("tìm phòng Cầu Giấy", None)

    ext_claude = ExternalProvider(provider="anthropic", model="claude-3-haiku", anthropic_api_key="dummy")
    with pytest.raises(RuntimeError, match="BLOCKED external LLM"):
        await ext_claude.extract("tìm phòng Cầu Giấy", None)


@pytest.mark.asyncio
async def test_local_uncertain_keeps_local_reply_when_external_is_disabled():
    """Khi tắt Gemini, Local trả CLARIFY/OUT_OF_SCOPE phải được giữ nguyên, không bị Rules ghi đè."""
    for intent, reply in [
        ("CLARIFY", "Bạn muốn tìm phòng ở khu vực nào và tầm giá bao nhiêu?"),
        ("OUT_OF_SCOPE", "Mình chỉ hỗ trợ tìm phòng trọ thôi ạ."),
    ]:
        local = make_provider_stub("local", result=ExtractResult(intent=intent, clarification_question=reply))
        rules = make_provider_stub("rules", result=ExtractResult(intent="SEARCH", filters=SearchFilters(price_max=2_000_000)))
        gw = Gateway([local, rules], {"local": CircuitBreaker()}, CallLogger())

        res, reason = await gw.extract(CallContext("req-1", "u-1", "EXTRACT"), "chỗ nào chill chill", None)
        assert res.intent == intent
        assert res.clarification_question == reply
        assert reason == "local:uncertain_intent"
        rules.extract.assert_not_awaited()
        assert gw.call_logger.records[0].is_final is True


@pytest.mark.asyncio
async def test_local_empty_search_converted_to_clarify_without_external():
    """Khi Local trả SEARCH rỗng không filter (chưa có last_filters), Gateway chuyển thành CLARIFY."""
    local = make_provider_stub("local", result=ExtractResult(intent="SEARCH", filters=SearchFilters()))
    rules = make_provider_stub("rules", result=ExtractResult(intent="SEARCH", filters=SearchFilters(price_max=3_000_000)))
    gw = Gateway([local, rules], {"local": CircuitBreaker()}, CallLogger())

    res, reason = await gw.extract(CallContext("req-2", "u-1", "EXTRACT"), "phòng ổn áp", None)
    assert res.intent == "CLARIFY"
    assert res.clarification_question
    assert reason == "local:uncertain_intent"
    rules.extract.assert_not_awaited()


# =============================================================================
# 2. CIRCUIT BREAKER & PROVIDER FAILURE / TIMEOUT / RETRY
# =============================================================================


def test_circuit_breaker_opens_after_threshold_and_recovers_after_cooldown():
    cb = CircuitBreaker(threshold=3, window=60, cooldown=1)
    assert cb.allow() is True
    cb.failure()
    cb.failure()
    assert cb.allow() is True
    cb.failure()
    assert cb.allow() is False  # Đủ 3 lỗi -> mở mạch

    # Hết cooldown -> half-open cho phép thăm dò
    cb.opened_at = time.time() - 2
    assert cb.allow() is True

    # Thành công -> đóng mạch, reset lỗi
    cb.success()
    assert cb.opened_at is None
    assert cb.failures == []


@pytest.mark.asyncio
async def test_gateway_opens_local_breaker_after_3_errors_and_routes_directly_to_rules():
    cb = CircuitBreaker(threshold=3, window=60, cooldown=60)
    local = make_provider_stub("local", extract_error=ProviderError("Ollama down"))
    rules = RulesProvider(catalogue_loader=lambda: {"wards": ["Phường Cầu Giấy"], "provinces": ["Hà Nội"], "amenities": ["Điều hòa"]})
    logger = CallLogger()
    gw = Gateway([local, rules], {"local": cb}, logger)

    for idx in range(3):
        res, reason = await gw.extract(CallContext(f"req-{idx}", "u-1", "EXTRACT"), "phòng dưới 3tr ở Cầu Giấy", None)
        assert res.intent == "SEARCH"
        assert res.filters.price_max == 3_000_000
        assert reason == "local:PROVIDER_ERROR"

    assert local.extract.await_count == 3
    assert cb.allow() is False

    # Lần thứ 4 phải bỏ qua Local (BREAKER_OPEN) và gọi thẳng Rules
    res4, reason4 = await gw.extract(CallContext("req-4", "u-1", "EXTRACT"), "phòng dưới 3tr ở Cầu Giấy", None)
    assert res4.intent == "SEARCH"
    assert local.extract.await_count == 3
    assert reason4 == "local:breaker_open"
    assert any(r.status == "BREAKER_OPEN" for r in logger.records)


@pytest.mark.asyncio
async def test_gateway_handles_timeout_invalid_json_invalid_value_and_all_failed():
    for exc, expected_status in [
        (asyncio.TimeoutError(), "TIMEOUT"),
        (InvalidJsonError("broken json"), "INVALID_JSON"),
        (InvalidValueError("price_min > price_max"), "INVALID_VALUE"),
        (ProviderError("HTTP 503"), "PROVIDER_ERROR"),
    ]:
        local = make_provider_stub("local", extract_error=exc)
        rules = make_provider_stub("rules", result=ExtractResult(intent="CLARIFY", clarification_question="Hỏi lại"))
        logger = CallLogger()
        gw = Gateway([local, rules], {"local": CircuitBreaker()}, logger)
        res, reason = await gw.extract(CallContext("r", "u", "EXTRACT"), "abc", None)
        assert res.intent == "CLARIFY"
        assert reason == f"local:{expected_status}"
        assert logger.records[0].status == expected_status

    # Nếu tất cả provider đều lỗi -> AllProvidersFailed
    local_fail = make_provider_stub("local", extract_error=ProviderError("down"))
    rules_fail = make_provider_stub("rules", extract_error=RuntimeError("unexpected"))
    gw_fail = Gateway([local_fail, rules_fail], {"local": CircuitBreaker()}, CallLogger())
    with pytest.raises(AllProvidersFailed):
        await gw_fail.extract(CallContext("r", "u", "EXTRACT"), "abc", None)


@pytest.mark.asyncio
async def test_local_provider_retries_once_on_invalid_json_then_succeeds():
    """LocalProvider tự sửa 1 lần khi model trả JSON sai schema."""
    provider = LocalProvider("http://localhost:11434/v1", "qwen3:4b-instruct-2507-q4_K_M")
    responses = [
        {"choices": [{"message": {"content": "Không phải JSON đâu"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
        {"choices": [{"message": {"content": '{"intent":"SEARCH","filters":{"price_max":2500000}}'}}], "usage": {"prompt_tokens": 20, "completion_tokens": 10}},
    ]
    call_idx = {"n": 0}

    async def fake_post(path, payload):
        idx = call_idx["n"]
        call_idx["n"] += 1
        return responses[idx]

    with patch.object(provider, "_post", side_effect=fake_post):
        res = await provider.extract("phòng 2tr5", None)
    assert call_idx["n"] == 2
    assert res.intent == "SEARCH"
    assert res.filters.price_max == 2_500_000


@pytest.mark.asyncio
async def test_local_provider_raises_invalid_json_after_two_bad_attempts():
    provider = LocalProvider("http://localhost:11434/v1", "qwen3:4b-instruct-2507-q4_K_M")

    async def always_bad(path, payload):
        return {"choices": [{"message": {"content": "{bad json"}}]}

    with patch.object(provider, "_post", side_effect=always_bad):
        with pytest.raises(InvalidJsonError):
            await provider.extract("phòng 2tr5", None)


# =============================================================================
# 3. VALIDATION ĐẦU VÀO (ChatRequest, SearchFilters, validate_extracted)
# =============================================================================


@pytest.mark.parametrize(
    "bad_message",
    [
        "",
        "   ",
        "\n\t\r  ",
        "a" * 2001,
        "phòng trọ " * 250,  # > 2000 ký tự
    ],
)
def test_chat_request_rejects_blank_and_oversized_messages(bad_message):
    with pytest.raises(ValidationError):
        ChatRequest(message=bad_message)


def test_chat_request_trims_whitespace_and_accepts_unicode_emojis():
    req = ChatRequest(message="   Tìm phòng Cầu Giấy dưới 3tr 🏠🔥   ")
    assert req.message == "Tìm phòng Cầu Giấy dưới 3tr 🏠🔥"

    with pytest.raises(ValidationError):
        ChatRequest(message="ok", conversation_id="x" * 37)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"price_min": -1},
        {"price_max": -100000},
        {"area_min": -5},
        {"area_max": -1},
        {"occupants": 0},
        {"occupants": -2},
        {"price_min": 5_000_000, "price_max": 2_000_000},
        {"area_min": 40, "area_max": 20},
    ],
)
def test_search_filters_rejects_negative_zero_occupants_and_inverted_ranges(kwargs):
    with pytest.raises(ValidationError):
        SearchFilters(**kwargs)


@pytest.mark.parametrize(
    "filters",
    [
        SearchFilters(price_max=100_000_001),
        SearchFilters(price_max=500_000_000),
        SearchFilters(area_max=1001),
        SearchFilters(area_max=2000),
    ],
)
def test_validate_extracted_rejects_absurd_price_and_area(filters):
    res = ExtractResult(intent="SEARCH", filters=filters)
    with pytest.raises(InvalidValueError):
        validate_extracted(res, None, {})


# =============================================================================
# 4. LÀM SẠCH PII (SĐT, EMAIL, CCCD) TRƯỚC KHI RA NGOÀI
# =============================================================================


@pytest.mark.parametrize(
    "raw,forbidden_fragments,must_keep",
    [
        ("Gọi mình số 0912345678 tìm phòng Cầu Giấy 3tr", ["0912345678"], "Cầu Giấy"),
        ("SĐT +84 912 345 678 hoặc 098.765.4321", ["912 345 678", "098.765.4321"], "SĐT"),
        ("Email sinh.vien+01@gmail.com cần thuê trọ", ["sinh.vien+01@gmail.com"], "thuê trọ"),
        ("CCCD 001203045678 và CMND 123456789 thuê ở Hà Đông", ["001203045678", "123456789"], "Hà Đông"),
        ("Phòng giá 3500000 đồng diện tích 25m2", [], "3500000"),  # Giá 7 chữ số KHÔNG bị che nhầm!
    ],
)
def test_sanitize_masks_pii_without_corrupting_prices(raw, forbidden_fragments, must_keep):
    cleaned = sanitize(raw)
    for secret in forbidden_fragments:
        assert secret not in cleaned
    if forbidden_fragments:
        assert "[ẩn]" in cleaned
    assert must_keep in cleaned


# =============================================================================
# 5. CÔ LẬP HỘI THOẠI (IDOR) & GIỚI HẠN QUOTA / CONCURRENT LEASE (429)
# =============================================================================


def test_conversation_idor_protection_returns_404_for_other_user():
    _, ai_engine, _, ai_factory = create_inmemory_dbs()
    try:
        with ai_factory() as session:
            cid = get_or_create_conversation(session, "user-A", None)
            assert get_or_create_conversation(session, "user-A", cid) == cid

            # User B cố truy cập conversation của User A -> 404
            with pytest.raises(HTTPException) as exc_info:
                get_or_create_conversation(session, "user-B", cid)
            assert exc_info.value.status_code == 404

            # conversation_id không tồn tại -> 404
            with pytest.raises(HTTPException) as exc_info2:
                get_or_create_conversation(session, "user-A", "non-existent-id")
            assert exc_info2.value.status_code == 404
    finally:
        ai_engine.dispose()


def test_rate_limit_daily_quota_and_concurrent_leases_and_cleanup():
    _, ai_engine, _, ai_factory = create_inmemory_dbs()
    tracker = UsageTracker()
    custom_settings = Settings(
        chat_daily_limit_per_user=4,
        max_concurrent_per_user=2,
        allow_external_fallback=False,
    )
    try:
        with patch("app.core.rate_limit.ai_session_factory", ai_factory), patch(
            "app.core.rate_limit.get_settings", return_value=custom_settings
        ):
            # 2 request song song đầu tiên OK
            tracker.reserve_chat("u-limit", "req-1")
            tracker.reserve_chat("u-limit", "req-2")

            # Request song song thứ 3 bị chặn 429 (max_concurrent=2) và KHÔNG làm mất lượt ngày
            with pytest.raises(HTTPException) as exc_conc:
                tracker.reserve_chat("u-limit", "req-3")
            assert exc_conc.value.status_code == 429
            assert "nhiều lượt chat" in exc_conc.value.detail

            # Giải phóng req-1 -> lại đặt được req-3
            tracker.release_chat("req-1")
            tracker.reserve_chat("u-limit", "req-3")

            # Mô phỏng lease cũ hết hạn -> tự động dọn khi reserve mới
            with ai_factory() as session, session.begin():
                lease = session.get(AiChatLease, "req-2")
                lease.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=10)
            tracker.reserve_chat("u-limit", "req-4")

            # Đã đủ 4 lượt ngày (req-1, req-2, req-3, req-4) -> req-5 bị 429 hết lượt hôm nay
            tracker.release_chat("req-3")
            tracker.release_chat("req-4")
            with pytest.raises(HTTPException) as exc_daily:
                tracker.reserve_chat("u-limit", "req-5")
            assert exc_daily.value.status_code == 429
            assert "hết lượt chat" in exc_daily.value.detail
    finally:
        ai_engine.dispose()


def test_jwt_verifier_rejects_expired_wrong_issuer_wrong_type_inactive_and_revoked_token():
    core_engine, ai_engine, core_factory, _ = create_inmemory_dbs()
    try:
        with core_factory() as session:
            session.add(CoreUser(id="user-1", email="u1@test.com", password="x", status="ACTIVE", auth_version=2))
            session.add(CoreUser(id="user-locked", email="u2@test.com", password="x", status="LOCKED", auth_version=1))
            session.commit()

        secret = "a" * 80
        settings = Settings(jwt_secret=secret, jwt_alg="HS512", jwt_issuer="rental-management", allow_external_fallback=False)
        verifier = JwtVerifier(settings)

        def make_token(overrides: dict):
            claims = {
                "sub": "user-1",
                "iss": "rental-management",
                "type": "access",
                "authVersion": 2,
                "scope": "ROLE_TENANT",
                "exp": int(time.time()) + 300,
            }
            claims.update(overrides)
            return jwt.encode(claims, secret, algorithm="HS512")

        with patch("app.core.security.core_session_factory", core_factory):
            # Token hợp lệ
            user = verifier.verify(make_token({}))
            assert user.id == "user-1"
            assert user.auth_version == 2
            assert "ROLE_TENANT" in user.scopes

            # Hết hạn -> AuthError
            with pytest.raises(AuthError):
                verifier.verify(make_token({"exp": int(time.time()) - 10}))

            # Sai issuer -> AuthError
            with pytest.raises(AuthError):
                verifier.verify(make_token({"iss": "fake-issuer"}))

            # Refresh token thay vì access token -> AuthError
            with pytest.raises(AuthError, match="access token"):
                verifier.verify(make_token({"type": "refresh"}))

            # Token đã bị thu hồi (authVersion cũ = 1 != DB = 2) -> AuthError
            with pytest.raises(AuthError, match="thu hồi"):
                verifier.verify(make_token({"authVersion": 1}))

            # Tài khoản bị khóa -> AuthError
            with pytest.raises(AuthError, match="không active"):
                verifier.verify(make_token({"sub": "user-locked", "authVersion": 1}))
    finally:
        core_engine.dispose()
        ai_engine.dispose()
