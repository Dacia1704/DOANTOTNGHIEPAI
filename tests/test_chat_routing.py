import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.api.chat import Turn, _turn_events
from app.chat.llm.base import ProviderError
from app.chat.llm.breaker import CircuitBreaker
from app.chat.llm.gateway import CallContext, Gateway
from app.chat.llm.rules_provider import RulesProvider
from app.chat.schemas import ExtractResult, SearchFilters
from app.chat.search import FilterResolutionError
from app.chat.small_talk import small_talk_reply
from app.observability.call_logger import CallLogger


def provider(name, result=None, error=None):
    stub = Mock(name=name)
    stub.name = name
    stub.model = "test-model"
    stub.last_usage = None
    stub.extract = AsyncMock(return_value=result, side_effect=error)
    return stub


class ChatRoutingTests(unittest.IsolatedAsyncioTestCase):
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

    async def extract(self, gateway, text="cau nay minh chua hieu", previous=None):
        return await gateway.extract(CallContext("request", "user", "EXTRACT"), text, previous)

    async def test_local_uncertain_intents_invoke_external(self):
        recovered = ExtractResult(intent="SEARCH", filters=SearchFilters(price_max=3000000))
        for intent in ("CLARIFY", "OUT_OF_SCOPE", "SEARCH", "FOLLOW_UP"):
            with self.subTest(intent=intent):
                local = provider("local", ExtractResult(intent=intent))
                external = provider("external", recovered)
                gateway = self.gateway(local, external)
                result, reason = await self.extract(gateway)
                self.assertEqual(result, recovered)
                self.assertEqual(reason, "local:uncertain_intent")
                external.extract.assert_awaited_once()
                self.assertFalse(gateway.call_logger.records[0].is_final)
                self.assertTrue(gateway.call_logger.records[1].is_final)
                self.assertFalse(gateway.breakers["local"].failures)

    async def test_understood_search_and_followup_stay_local(self):
        cases = (
            (ExtractResult(intent="SEARCH", filters=SearchFilters(price_max=3000000)), None),
            (ExtractResult(intent="FOLLOW_UP"), {"price_max": 3000000}),
        )
        for expected, previous in cases:
            with self.subTest(intent=expected.intent):
                external = provider("external")
                result, reason = await self.extract(
                    self.gateway(provider("local", expected), external), previous=previous,
                )
                self.assertEqual(result, expected)
                self.assertIsNone(reason)
                external.extract.assert_not_awaited()

    async def test_external_clarification_is_used_without_search(self):
        reply = "Bạn nói thêm một chút về chỗ ở bạn cần nhé?"
        gateway = self.gateway(
            provider("local", ExtractResult(intent="OUT_OF_SCOPE")),
            provider("external", ExtractResult(intent="CLARIFY", clarification_question=reply)),
        )
        turn = Turn("request", "conversation", "user", "chỗ ổn áp xíu á")
        events = [event async for event in _turn_events(turn, gateway, {}, {}, {}, {})]
        self.assertEqual(turn.parts, [reply])
        self.assertEqual(len(events), 1)
        self.assertEqual(turn.status, "CLARIFY")

    async def test_external_out_of_scope_uses_its_friendly_reply(self):
        reply = "Mình hỗ trợ về chỗ ở thôi. Bạn có cần tìm phòng không nhé?"
        gateway = self.gateway(
            provider("local", ExtractResult(intent="OUT_OF_SCOPE")),
            provider("external", ExtractResult(intent="OUT_OF_SCOPE", clarification_question=reply)),
        )
        turn = Turn("request", "conversation", "user", "dự báo thời tiết")
        _ = [event async for event in _turn_events(turn, gateway, {}, {}, {}, {})]
        self.assertEqual(turn.parts, [reply])

    async def test_external_failure_or_disabled_preserves_local_clarification(self):
        expected = ExtractResult(intent="CLARIFY", clarification_question="Bạn nói rõ hơn nhé?")
        for external in (None, provider("external", error=ProviderError("unavailable"))):
            with self.subTest(external=external is not None):
                gateway = self.gateway(provider("local", expected), external)
                result, _ = await self.extract(gateway)
                self.assertEqual(result, expected)
                self.assertTrue(gateway.call_logger.records[0].is_final)

    async def test_external_cap_and_breaker_are_respected(self):
        for blocked_by in ("cap", "breaker"):
            with self.subTest(blocked_by=blocked_by):
                expected = ExtractResult(intent="CLARIFY")
                external = provider("external")
                gateway = self.gateway(provider("local", expected), external, cap=lambda blocked_by=blocked_by: blocked_by == "cap")
                if blocked_by == "breaker":
                    for _ in range(3):
                        gateway.breakers["external"].failure()
                result, _ = await self.extract(gateway)
                self.assertEqual(result, expected)
                external.extract.assert_not_awaited()

    async def test_technical_failure_still_falls_back_to_rules(self):
        gateway = self.gateway(
            provider("local", error=ProviderError("offline")),
            provider("external", error=ProviderError("offline")),
        )
        result, _ = await self.extract(gateway, "phòng dưới 3tr")
        self.assertEqual(result.intent, "SEARCH")
        self.assertEqual(result.filters.price_max, 3000000)

    async def test_external_receives_sanitized_message_and_filters(self):
        external = provider("external", ExtractResult(intent="CLARIFY"))
        gateway = self.gateway(provider("local", ExtractResult(intent="CLARIFY")), external)
        await self.extract(gateway, "nhắn test@example.com", {"ward_or_area": "test@example.com"})
        text, filters = external.extract.call_args.args
        self.assertNotIn("test@example.com", text)
        self.assertNotIn("test@example.com", filters["ward_or_area"])

    async def test_small_talk_avoids_llm_and_preserves_search_context(self):
        gateway = Mock()
        previous = {"price_max": 3000000}
        turn = Turn("request", "conversation", "user", "Alo!!!")
        events = [event async for event in _turn_events(turn, gateway, previous, {}, {}, {})]
        self.assertEqual(turn.status, "OK")
        self.assertEqual(turn.filters, previous)
        self.assertIn("Chào bạn", turn.parts[0])
        gateway.extract.assert_not_called()
        self.assertEqual(json.loads(events[0].split("data: ", 1)[1])["text"], turn.parts[0])

    async def test_explicit_province_is_recovered_when_local_omits_or_misreads_it(self):
        catalogue = {"wards": ["Phường Hà Đông"], "provinces": ["Thành phố Hà Nội", "Hà Nam"]}
        for province in (None, "Hà Nam"):
            with self.subTest(province=province):
                local = provider("local", ExtractResult(intent="SEARCH", filters=SearchFilters(
                    ward_or_area="Hà Đông, Hà Nội", province=province,
                )))
                gateway = self.gateway(local)
                turn = Turn("request", "conversation", "user", "Tìm phòng ở Hà Đông, Hà Nội")
                with patch("app.api.chat._search", return_value=[]) as search:
                    _ = [event async for event in _turn_events(turn, gateway, {}, catalogue, {}, {})]
                filters = search.call_args.args[0]
                self.assertEqual(filters.province, "Thành phố Hà Nội")
                self.assertEqual(filters.ward_or_area, "Hà Đông")
                self.assertEqual(turn.status, "OK")

    async def test_clarification_retains_area_for_a_province_only_followup(self):
        catalogue = {"wards": ["Phường Hà Đông"], "provinces": ["Thành phố Hà Nội"]}
        local = provider("local", ExtractResult(intent="SEARCH", filters=SearchFilters(ward_or_area="Hà Đông")))
        turn = Turn("request", "conversation", "user", "Tìm phòng ở Hà Đông")
        with (
            patch("app.api.chat._search", side_effect=FilterResolutionError("Which province?")),
            self.assertRaises(FilterResolutionError),
        ):
            _ = [event async for event in _turn_events(turn, self.gateway(local), {}, catalogue, {}, {})]
        self.assertEqual(turn.filters["ward_or_area"], "Hà Đông")
        followup = Turn("request-2", "conversation", "user", "Hà Nội")
        local = provider("local", ExtractResult(intent="SEARCH", filters=SearchFilters(province="Hà Nội")))
        with patch("app.api.chat._search", return_value=[]) as search:
            _ = [event async for event in _turn_events(followup, self.gateway(local), turn.filters, catalogue, {}, {})]
        filters = search.call_args.args[0]
        self.assertEqual(filters.ward_or_area, "Hà Đông")
        self.assertEqual(filters.province, "Thành phố Hà Nội")


class SmallTalkTests(unittest.TestCase):
    def test_common_social_messages(self):
        for text in ("alo", "A lô!", "Xin chào bạn", "Hello", "hi", "bạn ơi", "Cảm ơn bạn nhé", "thanks", "Tạm biệt", "Bạn là ai?"):
            with self.subTest(text=text):
                self.assertIsNotNone(small_talk_reply(text))

    def test_greeting_with_search_criteria_is_not_consumed(self):
        for text in ("alo tìm phòng dưới 3tr", "chào bạn, mình cần phòng ở Cầu Giấy", "hi có wifi không", "nói gì vậy"):
            with self.subTest(text=text):
                self.assertIsNone(small_talk_reply(text))
