import json
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import chat as api
from app.api.deps import get_gateway
from app.chat.profile import detect_profile_action, profile_reply
from app.core.deps import get_current_tenant
from app.core.security import AuthenticatedUser
from app.db.models_ai import AiMessage
from app.db.models_core import Amenity, Province, RoomType, TenantProfile, TenantProfileAmenity, Ward
from app.chat.llm.gateway import Gateway
from app.observability.call_logger import CallLogger


@pytest.mark.parametrize(("text", "expected"), [
    ("Profile của tôi hiện tại gồm những điều kiện gì?", "SHOW"),
    ("Bạn có thể gợi ý phòng dựa trên profile của tôi được không?", "RECOMMEND"),
    ("Gợi ý phòng theo profile của tôi và cho biết hồ sơ hiện tại như thế nào?", "BOTH"),
    ("Tôi muốn xem hồ sơ hiện tại", "SHOW"),
    ("goi y phong theo ho so cua minh", "RECOMMEND"),
    ("Xem profile", "SHOW"),
    ("Tìm phòng dưới 3 triệu ở Cầu Giấy", None),
    ("Hồ sơ thuê phòng cần giấy tờ gì?", None),
    ("Không sử dụng profile của tôi để gợi ý", None),
])
def test_profile_intents(text, expected):
    assert detect_profile_action(text) == expected


@pytest.mark.parametrize("text", [
    "Bạn tư vấn phòng cho tôi nhé",
    "Bạn tư vấn phòng cho tôi nhé! 😊",
    "ban tu van phong cho toi nhe",
    "Bạn có thể tư vấn giúp mình một phòng trọ phù hợp được không?",
    "Chào bạn, mình đang cần tìm phòng trọ, giúp mình với!",
    "Mình muốn thuê phòng",
    "Tôi cần tìm nhà trọ",
    "Giúp mình tìm phòng",
    "Tìm phòng giúp tôi",
    "Gợi ý cho em một phòng phù hợp nhé",
    "Đề xuất phòng trọ cho mình",
    "Giới thiệu chỗ ở phù hợp với tôi",
    "Mình đang tìm nơi ở",
    "Tư vấn thuê trọ giúp mình với",
    "Có phòng nào phù hợp với mình không?",
    "Phòng nào phù hợp với tôi?",
    "Còn phòng phù hợp cho em không ạ?",
    "Tư vấn phòng theo hồ sơ của tôi",
])
def test_general_room_advice_uses_profile(text):
    assert detect_profile_action(text) == "RECOMMEND"


@pytest.mark.parametrize("text", [
    "Tư vấn phòng dưới 3 triệu ở Cầu Giấy cho tôi",
    "Bạn tư vấn phòng ở Hà Nội cho tôi nhé",
    "Gợi ý phòng có WiFi cho mình",
    "Tìm phòng giá rẻ",
    "Tôi cần thuê căn hộ 2 phòng ngủ",
    "Tìm phòng cho 2 người",
    "Có phòng nào phù hợp dưới 3 triệu không?",
    "Tư vấn hợp đồng thuê phòng cho tôi",
    "Tư vấn hồ sơ thuê phòng cần giấy tờ gì?",
    "Tôi có phòng trọ cho thuê",
    "Tôi không muốn tìm phòng",
    "Không dùng hồ sơ của tôi để tư vấn phòng",
    "Tư vấn phòng cho Bob",
    "Tư vấn phòng cho tôi nhé và cho biết hồ sơ của Bob",
    "Tư vấn chứng khoán cho tôi",
    "Bạn tư vấn giúp tôi nhé",
    "Xin chào",
])
def test_advice_with_conditions_or_other_topics_keeps_normal_extraction(text):
    assert detect_profile_action(text) is None


def _profiles(factory, *, enabled=True):
    with factory() as s:
        s.add(Province(id="p", code="01", name="Hà Nội"))
        s.flush()
        s.add(Ward(id="w", code="00166", name="Cầu Giấy", province_id="p"))
        s.add(RoomType(id="type", name="Phòng đơn"))
        s.add_all([Amenity(id="wifi", name="WiFi"), Amenity(id="ac", name="Điều hòa")])
        s.flush()
        s.add(TenantProfile(id="a", user_id="alice", desired_province_id="p", desired_ward_id="w",
            desired_room_type_id="type", price_min=1500000, price_max=3500000,
            area_min=18, area_max=30, occupants=2, move_in_date=date(2026, 11, 20),
            anchor_label="Trường học của Alice", anchor_latitude=21, anchor_longitude=105,
            max_distance_km=3, top_priority="PRICE", note="Ghi chú riêng của Alice",
            allow_personalization=enabled))
        s.add(TenantProfile(id="b", user_id="bob", price_max=99000000, note="SECRET BOB"))
        s.flush()
        s.add_all([
            TenantProfileAmenity(id="wifi-a", tenant_profile_id="a", amenity_id="wifi", is_required=True),
            TenantProfileAmenity(id="ac-a", tenant_profile_id="a", amenity_id="ac", is_required=False),
        ])
        s.commit()


def test_summary_is_current_owned_and_includes_actual_criteria_even_when_personalization_disabled(factory):
    _profiles(factory, enabled=False)
    with factory() as s:
        reply = profile_reply(s, "alice", "SHOW", request_id="r")
        assert "1,5 triệu" in reply.text and "3,5 triệu" in reply.text
        assert "Cầu Giấy" in reply.text and "Phòng đơn" in reply.text
        assert "Tiện ích bắt buộc: WiFi" in reply.text
        assert "Tiện ích ưu tiên: Điều hòa" in reply.text
        assert "20/11/2026" in reply.text and "Đang tắt" in reply.text
        assert "SECRET BOB" not in reply.text
        assert "anchor_latitude" not in reply.text and "105" not in reply.text
        s.get(TenantProfile, "a").price_max = 4000000
        s.commit()
    with factory() as s:
        assert "4 triệu" in profile_reply(s, "alice", "SHOW", request_id="next").text


def test_missing_empty_and_disabled_profiles_ask_for_details_without_recommending(factory, monkeypatch):
    import app.chat.profile as module
    monkeypatch.setattr(module, "recommend", lambda *a, **kw: pytest.fail("Should not recommend"))
    _profiles(factory, enabled=False)
    with factory() as s:
        assert profile_reply(s, "missing", "SHOW", request_id="r").status == "CLARIFY"
        reply = profile_reply(s, "alice", "RECOMMEND", request_id="r")
        assert reply.items == [] and "tắt cá nhân hóa" in reply.text
        s.add(TenantProfile(id="empty", user_id="empty", allow_personalization=True))
        s.commit()
        assert profile_reply(s, "empty", "RECOMMEND", request_id="r").status == "CLARIFY"


@pytest.mark.parametrize("profile_state", ["enabled", "disabled", "missing", "empty"])
def test_general_advice_sse_uses_authenticated_profile_without_calling_llm(
        factory, monkeypatch, profile_state):
    from types import SimpleNamespace

    import app.chat.profile as profile_module
    import app.core.rate_limit as limits

    if profile_state != "missing":
        _profiles(factory, enabled=profile_state != "disabled")
    if profile_state == "empty":
        with factory() as s:
            s.add(TenantProfile(id="empty", user_id="empty", allow_personalization=True))
            s.commit()
    user_id = "empty" if profile_state == "empty" else "alice"
    monkeypatch.setattr(api, "core_session_factory", factory)
    monkeypatch.setattr(api, "ai_session_factory", factory)
    monkeypatch.setattr(limits, "ai_session_factory", factory)
    recommended_users = []

    def recommend(session, owner_id, **kwargs):
        assert profile_state == "enabled"
        recommended_users.append(owner_id)
        return SimpleNamespace(items=[SimpleNamespace(
            listing_id="listing-a", property_id="property-a", matched_room_id="room-a",
            rental_mode="BY_ROOM", title="Phòng phù hợp Alice", price=2500000,
            area_m2=22, ward_name="Cầu Giấy", room_type_name="Phòng đơn",
            address_line="Địa chỉ phòng", amenities=["WiFi"],
        )])

    monkeypatch.setattr(profile_module, "recommend", recommend)

    class NoModels:
        name = "local"

        async def extract(self, *args):
            pytest.fail("General advice should use the profile before LLM extraction")

        async def compose(self, *args):
            pytest.fail("Profile recommendation should not send private data to LLM")
            yield "unreachable"

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_current_tenant] = lambda: AuthenticatedUser(user_id, 0, ("ROLE_TENANT",))
    app.dependency_overrides[get_gateway] = lambda: Gateway([NoModels()], {}, CallLogger())
    response = TestClient(app).post("/ai/chat", json={
        "message": "Bạn tư vấn phòng cho tôi nhé", "user_id": "bob",
    })
    assert response.status_code == 200
    assert "SECRET BOB" not in response.text
    frames = [json.loads(frame.split("data: ", 1)[1])
              for frame in response.text.strip().split("\n\n")]
    done = frames[-1]
    with factory() as s:
        message = s.get(AiMessage, done["message_id"])
        assert message.intent == "PROFILE_RECOMMEND"
        assert message.extract_provider is None and message.compose_provider is None
        if profile_state == "enabled":
            assert done["status"] == "OK"
            assert recommended_users == ["alice"]
            assert message.filters_json["price_max"] == 3500000
            assert message.listing_ids == ["listing-a"]
            assert any(frame.get("items", [{}])[0].get("match_type") == "PROFILE"
                       for frame in frames)
            assert "dựa trên hồ sơ hiện tại" in message.content
        else:
            assert done["status"] == "CLARIFY"
            assert not recommended_users and message.listing_ids == []
            expected = {"disabled": "tắt cá nhân hóa", "missing": "chưa lưu hồ sơ",
                        "empty": "chưa có tiêu chí"}[profile_state]
            assert expected in message.content


def test_profile_sse_never_calls_models_preserves_filters_and_accepts_consent_followup(factory, monkeypatch):
    import app.core.rate_limit as limits
    import app.chat.profile as profile_module

    _profiles(factory)
    monkeypatch.setattr(api, "core_session_factory", factory)
    monkeypatch.setattr(api, "ai_session_factory", factory)
    monkeypatch.setattr(limits, "ai_session_factory", factory)
    recommendations = []

    def recommend(session, user_id, **kwargs):
        from types import SimpleNamespace
        recommendations.append(user_id)
        return SimpleNamespace(items=[])

    monkeypatch.setattr(profile_module, "recommend", recommend)

    class NoModels:
        name = "external"

        async def extract(self, *args):
            pytest.fail("Private profile must not be sent to a model")

        async def compose(self, *args):
            pytest.fail("Private profile must not be sent to a model")
            yield "unreachable"

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_current_tenant] = lambda: AuthenticatedUser("alice", 0, ("ROLE_TENANT",))
    gateways = []

    def gateway():
        instance = Gateway([NoModels()], {}, CallLogger())
        gateways.append(instance)
        return instance

    app.dependency_overrides[get_gateway] = gateway
    client = TestClient(app)
    response = client.post("/ai/chat", json={"message": "Profile của tôi hiện tại gồm những điều kiện gì?",
                                              "user_id": "bob"})
    assert response.status_code == 200 and "SECRET BOB" not in response.text
    frames = [json.loads(frame.split("data: ", 1)[1]) for frame in response.text.strip().split("\n\n")]
    done = frames[-1]
    assert done["status"] == "OK"
    with factory() as s:
        message = s.get(AiMessage, done["message_id"])
        assert message.intent == "PROFILE_SHOW"
        assert message.extract_provider is None
    response = client.post("/ai/chat", json={"message": "Có, gợi ý đi nhé!",
                                              "conversation_id": done["conversation_id"]})
    assert recommendations == ["alice"]
    assert all(not gateway.call_logger.records for gateway in gateways)
    with factory() as s:
        messages = list(s.scalars(select(AiMessage).where(AiMessage.role == "ASSISTANT")))
        assert any(message.intent == "PROFILE_RECOMMEND" and message.filters_json["price_max"] == 3500000
                   for message in messages)
