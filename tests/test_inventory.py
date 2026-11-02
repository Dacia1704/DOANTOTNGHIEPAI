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
