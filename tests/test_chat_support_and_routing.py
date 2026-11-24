"""Phần 2: Trao đổi, thông báo và quản trị bổ trợ.

Kiểm thử:
- Định tuyến ý định chat và phản hồi nhanh Small Talk.
- Xác thực JWT, phân quyền các role (TENANT, LANDLORD, ADMIN).
- Chuỗi fallback 3 tầng: Local LLM -> External Fallback (Gemini/Anthropic) -> Rules Provider.
- Cơ chế bảo vệ: Circuit Breaker, Daily Cap, làm sạch dữ liệu PII trước khi gửi ra ngoài.
"""

import inspect
import json
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

import jwt
from fastapi import HTTPException

from app.api.chat import Turn, _turn_events, chat
from app.api.events import record_event, record_feedback
from app.api.recommendations import recommendations
from app.chat.llm.base import ProviderError
from app.chat.llm.breaker import CircuitBreaker
from app.chat.llm.gateway import CallContext, Gateway
from app.chat.llm.rules_provider import RulesProvider
from app.chat.schemas import ExtractResult, SearchFilters
from app.chat.search import FilterResolutionError
from app.chat.small_talk import small_talk_reply
from app.config import Settings
from app.core.deps import get_current_user
from app.core.security import JwtVerifier
from app.observability.call_logger import CallLogger


def mock_provider(name, result=None, error=None):
    stub = Mock(name=name)
    stub.name = name
    stub.model = "test-model"
    stub.last_usage = None
    stub.extract = AsyncMock(return_value=result, side_effect=error)
    return stub


class ChatRoutingAndFallbackTests(unittest.IsolatedAsyncioTestCase):
    """Kiểm thử định tuyến ý định chat và chuỗi fallback độ tin cậy cao."""

    def gateway(self, local, external=None, cap=None):
        chain = [local]
        if external is not None:
            chain.append(external)
        chain.append(RulesProvider())
        return Gateway(
            chain,
            {"local": CircuitBreaker(), "external": CircuitBreaker()},
            CallLogger(),
            external_cap_check=cap,
        )

    async def extract(self, gateway, text="câu này mình chưa hiểu", previous=None):
        return await gateway.extract(CallContext("request", "user", "EXTRACT"), text, previous)

    async def test_local_uncertain_intents_invoke_external_fallback(self):
        """Khi local trả về CLARIFY, OUT_OF_SCOPE hoặc SEARCH thiếu bộ lọc, Gateway kích hoạt API ngoài."""
        recovered = ExtractResult(intent="SEARCH", filters=SearchFilters(price_max=3000000))
        for intent in ("CLARIFY", "OUT_OF_SCOPE", "SEARCH", "FOLLOW_UP"):
            with self.subTest(intent=intent):
                local = mock_provider("local", ExtractResult(intent=intent))
                external = mock_provider("external", recovered)
                gateway = self.gateway(local, external)
                result, reason = await self.extract(gateway)
                self.assertEqual(result, recovered)
                self.assertEqual(reason, "local:uncertain_intent")
                external.extract.assert_awaited_once()

    async def test_understood_search_stays_local(self):
        """Khi local hiểu rõ bộ lọc cụ thể, không cần gọi API ngoài."""
        expected = ExtractResult(intent="SEARCH", filters=SearchFilters(price_max=3000000))
        external = mock_provider("external")
        gateway = self.gateway(mock_provider("local", expected), external)
        result, reason = await self.extract(gateway)
        self.assertEqual(result, expected)
        self.assertIsNone(reason)
        external.extract.assert_not_awaited()

    async def test_external_clarification_is_used_directly(self):
        """Câu hỏi làm rõ từ API ngoài được gửi trực tiếp tới người dùng."""
        reply = "Bạn nói thêm một chút về ngân sách hoặc khu vực bạn cần nhé?"
        gateway = self.gateway(
            mock_provider("local", ExtractResult(intent="OUT_OF_SCOPE")),
            mock_provider("external", ExtractResult(intent="CLARIFY", clarification_question=reply)),
        )
        turn = Turn("request", "conversation", "user", "chỗ nào ổn áp tí")
        events = [event async for event in _turn_events(turn, gateway, {}, {}, {}, {})]
        self.assertEqual(turn.parts, [reply])
        self.assertEqual(len(events), 1)
        self.assertEqual(turn.status, "CLARIFY")

    async def test_external_cap_and_circuit_breaker_are_respected(self):
        """Khi chạm daily cap hoặc circuit breaker mở, hệ thống không gọi API ngoài."""
        for blocked_by in ("cap", "breaker"):
            with self.subTest(blocked_by=blocked_by):
                expected = ExtractResult(intent="CLARIFY", clarification_question="Hỏi lại")
                external = mock_provider("external")
                gateway = self.gateway(
                    mock_provider("local", expected),
                    external,
                    cap=lambda blocked_by=blocked_by: blocked_by == "cap",
                )
                if blocked_by == "breaker":
                    for _ in range(3):
                        gateway.breakers["external"].failure()
                result, _ = await self.extract(gateway)
                self.assertEqual(result, expected)
                external.extract.assert_not_awaited()

    async def test_technical_failure_falls_back_to_rules(self):
        """Khi cả local và external đều lỗi kết nối, hệ thống tự động fallback sang Rules trích xuất."""
        gateway = self.gateway(
            mock_provider("local", error=ProviderError("offline")),
            mock_provider("external", error=ProviderError("offline")),
        )
        result, _ = await self.extract(gateway, "phòng dưới 3tr")
        self.assertEqual(result.intent, "SEARCH")
        self.assertEqual(result.filters.price_max, 3000000)

    async def test_external_receives_sanitized_pii_content(self):
        """Email và số điện thoại cá nhân được che giấu (masking) trước khi gửi tới API bên ngoài."""
        external = mock_provider("external", ExtractResult(intent="CLARIFY"))
        gateway = self.gateway(mock_provider("local", ExtractResult(intent="CLARIFY")), external)
        await self.extract(gateway, "liên hệ qua email secret@example.com nhé", {"ward_or_area": "secret@example.com"})
        text, filters = external.extract.call_args.args
        self.assertNotIn("secret@example.com", text)
        self.assertNotIn("secret@example.com", filters["ward_or_area"])


class SmallTalkTests(unittest.TestCase):
    """Kiểm thử xử lý giao tiếp nhanh (Small talk) không tiêu tốn tài nguyên LLM."""

    def test_social_messages_handled_instantly(self):
        """Các câu chào hỏi, cảm ơn, tạm biệt, hỏi trợ lý trả lời ngay."""
        phrases = (
            "alo", "A lô!", "Xin chào bạn", "Hello", "hi", "bạn ơi",
            "Cảm ơn bạn nhé", "thanks", "Tạm biệt", "Bạn là ai?",
        )
        for text in phrases:
            with self.subTest(text=text):
                reply = small_talk_reply(text)
                self.assertIsNotNone(reply)
                self.assertTrue(len(reply) > 0)

    def test_greeting_with_search_criteria_is_not_consumed_as_small_talk(self):
        """Câu chào kèm tiêu chí tìm kiếm phải chuyển vào luồng trích bộ lọc, không được chặn bởi small talk."""
        phrases = (
            "alo tìm phòng dưới 3tr",
            "chào bạn, mình cần phòng ở Cầu Giấy",
            "hi có wifi không",
            "hello có phòng ban công không bạn",
        )
        for text in phrases:
            with self.subTest(text=text):
                self.assertIsNone(small_talk_reply(text))


class JwtAuthAndAccessTests(unittest.TestCase):
    """Kiểm thử bảo mật JWT HS512 và phân quyền người dùng."""

    def setUp(self):
        self.settings = Settings(_env_file=None, jwt_secret="test-secret-at-least-64-bytes-long-for-hs512-compliance-rental-mgmt")
        self.verifier = JwtVerifier(self.settings)
        self.verifier_patch = patch("app.core.deps.get_verifier", return_value=self.verifier)
        self.verifier_patch.start()
        self.addCleanup(self.verifier_patch.stop)

    def generate_token(self, role, **overrides):
        claims = {
            "sub": "test-user-id",
            "iss": self.settings.jwt_issuer,
            "exp": int(time.time()) + 300,
            "type": "access",
            "authVersion": 1,
            "scope": f"ROLE_{role}",
        }
        claims.update(overrides)
        return jwt.encode(claims, self.settings.jwt_secret, algorithm="HS512")

    def test_all_active_roles_can_access_ai(self):
        """Cả 3 vai trò TENANT, LANDLORD, ADMIN khi ACTIVE đều được xác thực truy cập AI."""
        with patch.object(self.verifier._cache, "get", return_value=(1, "ACTIVE")):
            for role in ("TENANT", "LANDLORD", "ADMIN"):
                with self.subTest(role=role):
                    token = self.generate_token(role)
                    user = get_current_user(f"Bearer {token}")
                    self.assertEqual(user.id, "test-user-id")
                    self.assertIn(f"ROLE_{role}", user.scopes)

    def test_invalid_or_missing_tokens_rejected_with_401(self):
        """Token thiếu hoặc sai secret bị từ chối với mã lỗi HTTP 401."""
        for auth_header in (None, "Bearer invalid-token-string", "Token wrong-format"):
            with self.subTest(auth=auth_header):
                with self.assertRaises(HTTPException) as ctx:
                    get_current_user(auth_header)
                self.assertEqual(ctx.exception.status_code, 401)

    def test_expired_jwt_rejected_with_401(self):
        """JWT đã hết hạn (exp trong quá khứ) bị từ chối với 401."""
        token = self.generate_token("TENANT", exp=int(time.time()) - 60)
        with self.assertRaises(HTTPException) as ctx:
            get_current_user(f"Bearer {token}")
        self.assertEqual(ctx.exception.status_code, 401)

    def test_blocked_user_rejected_with_401(self):
        """Tài khoản đã bị khóa (BLOCKED) bị chặn truy cập AI ngay cả khi JWT còn hạn."""
        token = self.generate_token("TENANT")
        with patch.object(self.verifier._cache, "get", return_value=(1, "BLOCKED")):
            with self.assertRaises(HTTPException) as ctx:
                get_current_user(f"Bearer {token}")
            self.assertEqual(ctx.exception.status_code, 401)

    def test_mismatched_auth_version_rejected_with_401(self):
        """JWT có authVersion cũ (sau khi đổi mật khẩu hoặc bị thu hồi phiên) bị từ chối 401."""
        token = self.generate_token("TENANT", authVersion=1)
        with patch.object(self.verifier._cache, "get", return_value=(2, "ACTIVE")):
            with self.assertRaises(HTTPException) as ctx:
                get_current_user(f"Bearer {token}")
            self.assertEqual(ctx.exception.status_code, 401)

    def test_circuit_breaker_opens_after_failure_threshold(self):
        """CircuitBreaker chuyển sang trạng thái mở (chặn gọi tiếp) sau khi vượt ngưỡng lỗi liên tiếp."""
        breaker = CircuitBreaker(threshold=2, cooldown=60)
        self.assertTrue(breaker.allow())
        breaker.failure()
        self.assertTrue(breaker.allow())
        breaker.failure()
        self.assertFalse(breaker.allow())
        breaker.success()
        self.assertTrue(breaker.allow())
