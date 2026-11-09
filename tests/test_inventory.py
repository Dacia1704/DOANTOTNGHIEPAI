from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.chat.schemas import SearchFilters
from app.chat.search import (
    FilterResolutionError,
    fetch_listing_cards,
    resolve_locations,
)
from app.db.models_core import (
    Amenity,
    Listing,
    Property,
    PropertyAmenity,
    Province,
    Room,
    RoomAmenity,
    RoomType,
    Ward,
)
from app.recommend.candidates import fetch_candidates


@pytest.fixture
def inventory(factory):
    now = datetime.now()
    with factory() as s:
        s.add(Province(id="p", code="P", name="Ha Noi"))
        s.flush()
        s.add(Ward(id="w", code="W", name="Cau Giay", province_id="p"))
        s.add(RoomType(id="t", name="Phong don", description=None, parent_id=None))
        s.add(
            Property(
                id="house",
                name="House",
                landlord_id="owner",
                property_type_id="pt",
                rental_mode="BY_ROOM",
                province_id="p",
                ward_id="w",
                address_line="Address",
            )
        )
        s.flush()
        for rid, price, area in [
            ("small", 2000000, 12),
            ("large", 4000000, 30),
            ("match", 2800000, 26),
        ]:
            s.add(
                Room(
                    id=rid,
                    property_id="house",
                    room_number=rid,
                    room_type_id="t",
                    price=price,
                    area_m2=area,
                    max_occupants=2,
                    rental_status="AVAILABLE",
                )
            )
        s.add_all([Amenity(id="wifi", name="Wifi"), Amenity(id="ac", name="AC")])
        s.flush()
        s.add(PropertyAmenity(id="pa", property_id="house", amenity_id="wifi"))
        s.add(RoomAmenity(id="ra", room_id="match", amenity_id="ac"))
        for lid, days, expiry in [
            ("old", 5, 10),
            ("current", 1, 10),
            ("expired", 0, -1),
        ]:
            s.add(
                Listing(
                    id=lid,
                    property_id="house",
                    title=lid,
                    status="PUBLISHED",
                    published_at=now - timedelta(days=days),
                    created_at=now - timedelta(days=days),
                    expires_at=now + timedelta(days=expiry),
                )
            )
        s.commit()
    return factory


def test_filters_apply_to_one_room_and_latest_listing(inventory):
    with inventory() as s:
        cards = fetch_listing_cards(
            s, SearchFilters(price_max=3000000, area_min=25), amenity_ids=["wifi", "ac"]
        )
        assert [(c.id, c.matched_room_id, c.price) for c in cards] == [
            ("current", "match", Decimal(2800000))
        ]
        assert set(cards[0].amenities) == {"Wifi", "AC"}
        assert (
            fetch_listing_cards(s, SearchFilters(price_max=2500000, area_min=25)) == []
        )


def test_required_amenities_do_not_leak_between_rooms(inventory):
    with inventory() as s:
        candidates = fetch_candidates(s, required_amenity_ids=["ac", "wifi"])
        assert [c.room_id for c in candidates] == ["match"]
        assert all(c.listing_id == "current" for c in candidates)
        with pytest.raises(FilterResolutionError):
            resolve_locations(s, "unknown place")


def test_recommendation_deduplicates_listing(inventory, monkeypatch):
    import app.recommend.service as service

    monkeypatch.setattr(service, "_log_request", lambda *a, **kw: None)
    with inventory() as s:
        result = service.recommend(s, "tenant")
        assert len(result.items) == 1
        assert result.items[0].listing_id == "current"
        assert result.items[0].matched_room_id in {"small", "large", "match"}


def test_models_match_be_columns():
    import re
    from pathlib import Path

    from app.db.models_core import CoreBase

    base = (
        Path(__file__).resolve().parents[2]
        / "RentalManagement/src/main/resources/db/migration"
    )
    ddl = (base / "V1__fullsql.sql").read_text(encoding="utf-8")
    for name in ("listings", "rooms", "properties", "property_amenities"):
        body = re.search(r"CREATE TABLE `" + name + r"`\s*\((.*?)\) ENGINE", ddl, re.S)[
            1
        ]
        cols = set(re.findall(r"^\s*`(\w+)`\s", body, re.M))
        assert set(CoreBase.metadata.tables[name].columns.keys()) <= cols


def test_search_chat_persists_results_usage_and_room_identity(inventory, monkeypatch):
    import json

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from app.api import chat as api
    from app.api.deps import get_gateway
    from app.chat.llm.gateway import Gateway
    from app.chat.llm.rules_provider import RulesProvider
    from app.chat.schemas import ExtractResult
    from app.core import rate_limit
    from app.core.deps import get_current_tenant
    from app.core.security import AuthenticatedUser
    from app.db.models_ai import AiMessage, AiUsageDaily
    from app.observability.call_logger import CallLogger

    class Local(RulesProvider):
        name = "local"

        async def extract(self, text, previous):
            return ExtractResult(
                intent="SEARCH",
                filters=SearchFilters(
                    ward_or_area="Cau Giay",
                    price_max=3000000,
                    area_min=25,
                    amenities=["Wifi", "AC"],
                ),
            )

    monkeypatch.setattr(api, "core_session_factory", inventory)
    monkeypatch.setattr(api, "ai_session_factory", inventory)
    monkeypatch.setattr(rate_limit, "ai_session_factory", inventory)
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_current_tenant] = lambda: AuthenticatedUser(
        "tenant", 0, ("ROLE_TENANT",)
    )
    app.dependency_overrides[get_gateway] = lambda: Gateway([Local()], {}, CallLogger())
    response = TestClient(app).post("/ai/chat", json={"message": "Find room"})
    assert response.status_code == 200
    frames = [
        (frame.splitlines()[0], json.loads(frame.split("data: ", 1)[1]))
        for frame in response.text.strip().split("\n\n")
    ]
    listings = next(
        data["items"] for event, data in frames if event == "event: listings"
    )
    assert [(i["id"], i["matched_room_id"]) for i in listings] == [("current", "match")]
    assert frames[-1][1]["status"] == "OK"
    with inventory() as s:
        message = s.get(AiMessage, frames[-1][1]["message_id"])
        assert message.listing_ids == ["current"]
        assert message.compose_provider == "local"
        assert (
            s.scalar(
                select(AiUsageDaily).where(AiUsageDaily.user_id == "tenant")
            ).local_count
            == 2
        )


def _add_neighbor(session, key, *, lat=21.005, price=2800000, wifi=True):
    session.add(Ward(id=key, code=key, name=key, province_id="p"))
    session.flush()
    session.add(Property(id=key, landlord_id="owner", property_type_id="pt", name=key,
                         rental_mode="BY_ROOM", province_id="p", ward_id=key,
                         address_line=key, latitude=lat, longitude=105))
    session.flush()
    session.add(Room(id=key, property_id=key, room_number="1", room_type_id="t",
                     price=price, area_m2=30, max_occupants=2, rental_status="AVAILABLE"))
    session.add(Listing(id=key, property_id=key, title=key, status="PUBLISHED",
                        published_at=datetime.now(), created_at=datetime.now(),
                        expires_at=datetime.now() + timedelta(days=10)))
    if wifi:
        session.add(PropertyAmenity(id=key, property_id=key, amenity_id="wifi"))
    session.flush()


def _prepare_nearby(session):
    from sqlalchemy import update
    session.execute(update(Room).values(rental_status="RENTED"))
    ward = session.get(Ward, "w")
    ward.centroid_lat, ward.centroid_lng = 21, 105
    session.flush()


def test_exact_match_wins_over_cheaper_nearby(inventory):
    from app.chat.suggestions import search_with_suggestions
    with inventory() as s:
        s.get(Ward, "w").centroid_lat = 21
        s.get(Ward, "w").centroid_lng = 105
        _add_neighbor(s, "near", price=1500000)
        result = search_with_suggestions(s, SearchFilters(
            ward_or_area="Cau Giay", price_max=3000000, area_min=25, amenities=["Wifi"]))
        assert [item["id"] for item in result] == ["current"]
        assert "match_type" not in result[0]


def test_nearby_uses_centroid_actual_distance_amenities_and_orders_before_limit(inventory):
    from app.chat.suggestions import search_with_suggestions
    with inventory() as s:
        _prepare_nearby(s)
        _add_neighbor(s, "near", lat=21.005)
        _add_neighbor(s, "farther", lat=21.02)
        _add_neighbor(s, "too_far", lat=21.1)
        _add_neighbor(s, "no_wifi", lat=21.001, wifi=False)
        filters = SearchFilters(ward_or_area="Cau Giay", price_max=3000000, amenities=["Wifi"])
        result = search_with_suggestions(s, filters, limit=1)
        assert [item["id"] for item in result] == ["near"]
        assert result[0]["match_type"] == "NEARBY"
        assert 0.5 < result[0]["distance_km"] < 0.6
        assert filters.price_max == 3000000 and filters.ward_or_area == "Cau Giay"


def test_missing_centroid_does_not_invent_nearby_matches(inventory):
    from app.chat.suggestions import search_with_suggestions
    with inventory() as s:
        _prepare_nearby(s)
        s.get(Ward, "w").centroid_lat = None
        s.get(Ward, "w").centroid_lng = None
        _add_neighbor(s, "near")
        result = search_with_suggestions(s, SearchFilters(
            ward_or_area="Cau Giay", price_max=3000000, amenities=["Wifi"]))
        assert result == []


def test_nearby_price_alternative_is_labelled_and_preserves_requirements(inventory):
    from app.chat.suggestions import compose_suggestions, search_with_suggestions
    with inventory() as s:
        _prepare_nearby(s)
        _add_neighbor(s, "over", price=3300000)
        _add_neighbor(s, "over_cap", price=3700000)
        _add_neighbor(s, "no_wifi", price=3100000, wifi=False)
        filters = SearchFilters(ward_or_area="Cau Giay", price_max=3000000, amenities=["Wifi"])
        result = search_with_suggestions(s, filters)
        assert [item["id"] for item in result] == ["over"]
        assert result[0]["match_type"] == "NEARBY_PRICE"
        assert len(result[0]["differences"]) == 2
        reply = compose_suggestions(result, filters)
        assert "300,000" in reply and "Wifi" in reply
        assert filters.price_max == 3000000


def test_same_ward_price_alternative_and_no_result_question(inventory):
    from app.chat.suggestions import compose_suggestions, search_with_suggestions
    with inventory() as s:
        s.get(Room, "large").price = 3300000
        s.flush()
        filters = SearchFilters(ward_or_area="Cau Giay", price_max=3000000,
                                area_min=29, amenities=["Wifi"])
        result = search_with_suggestions(s, filters)
        assert result[0]["match_type"] == "PRICE"
        assert result[0]["matched_room_id"] == "large"
        assert "distance_km" not in result[0]
        assert "3.6" in compose_suggestions([], filters)


@pytest.mark.asyncio
async def test_sse_nearby_keeps_original_filters_and_skips_llm_rewording(inventory, monkeypatch):
    import json
    from app.api import chat as api
    from app.chat.llm.gateway import Gateway
    from app.chat.schemas import ExtractResult
    from app.observability.call_logger import CallLogger

    with inventory() as s:
        _prepare_nearby(s)
        _add_neighbor(s, "over", price=3300000)
        s.commit()

    class Local:
        name = "local"
        model = "test"

        async def extract(self, text, previous):
            return ExtractResult(intent="SEARCH", filters=SearchFilters(
                ward_or_area="Cau Giay", price_max=3000000, amenities=["Wifi"]))

        async def compose(self, text, listings):
            raise AssertionError("Alternative explanations must remain factual")
            yield "unreachable"

    monkeypatch.setattr(api, "core_session_factory", inventory)
    gateway = Gateway([Local()], {}, CallLogger())
    turn = api.Turn("r", "c", "tenant", "find rooms")
    frames = [frame async for frame in api._turn_events(turn, gateway, None, {}, {}, {})]
    event = next(frame for frame in frames if frame.startswith("event: listings"))
    cards = json.loads(event.split("data: ", 1)[1])["items"]
    assert cards[0]["match_type"] == "NEARBY_PRICE"
    assert turn.filters["price_max"] == 3000000
    assert turn.listing_ids == ["over"]
    assert turn.status == "OK"
    assert len(gateway.call_logger.records) == 1


def test_profile_chat_reuses_actual_recommender_and_logs_owned_snapshot(inventory, monkeypatch):
    import app.db.session as db
    from app.chat.profile import profile_reply
    from app.db.models_ai import RecommendationRequest
    from app.db.models_core import TenantProfile, TenantProfileAmenity

    monkeypatch.setattr(db, "ai_session_factory", inventory)
    with inventory() as s:
        s.add(TenantProfile(id="profile", user_id="tenant", desired_province_id="p",
            desired_ward_id="w", desired_room_type_id="t", price_min=1500000, price_max=3000000,
            area_min=25, area_max=30, occupants=2, allow_personalization=True,
            note="PRIVATE NOTE"))
        s.flush()
        s.add(TenantProfileAmenity(id="required", tenant_profile_id="profile", amenity_id="ac", is_required=True))
        s.add(TenantProfileAmenity(id="preferred", tenant_profile_id="profile", amenity_id="wifi", is_required=False))
        s.commit()
        reply = profile_reply(s, "tenant", "RECOMMEND", request_id="profile-chat-request")
        assert [item["id"] for item in reply.items] == ["current"]
        assert reply.items[0]["matched_room_id"] == "match"
        assert "AC" in reply.items[0]["amenities"]
        assert "PRIVATE NOTE" not in reply.text
        assert reply.filters["amenities"] == ["AC"]
    with inventory() as s:
        request = s.get(RecommendationRequest, "profile-chat-request")
        assert request.user_id == "tenant" and request.source == "CHATBOT"
        assert request.listing_ids == ["current"]
        assert "PRIVATE NOTE" not in str(request.profile_snapshot)
