"""Shared test fixtures, SQLite rental DB seeders, and strict Gemini/External API network blocker.

Guarantees ZERO external LLM API calls (Gemini / Anthropic) during testing.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.db.models_ai import AiBase
from app.db.models_core import (
    Amenity,
    CoreBase,
    Listing,
    Property,
    PropertyAmenity,
    Province,
    Room,
    RoomAmenity,
    RoomType,
    TenantProfile,
    TenantProfileAmenity,
    TenantProfileWard,
    Ward,
)

BLOCKED_EXTERNAL_HOSTS = (
    "generativelanguage.googleapis.com",
    "api.anthropic.com",
    "openai.com",
)

REAL_AMENITY_NAMES = [
    "Ban công",
    "Bếp riêng",
    "Bình nước nóng",
    "Camera an ninh",
    "Chỗ để xe",
    "Cửa sổ",
    "Điều hòa",
    "Giường",
    "Máy giặt dùng chung",
    "Nhà vệ sinh riêng",
    "Thang máy",
    "Tủ lạnh",
    "Tủ quần áo",
    "WiFi",
]

SAMPLE_CATALOGUE = {
    "provinces": [
        "Thành phố Hà Nội",
        "Thành phố Hồ Chí Minh",
        "Thành phố Đà Nẵng",
        "Thành phố Cần Thơ",
        "Thành phố Hải Phòng",
        "Thành phố Huế",
        "Tỉnh Quảng Trị",
    ],
    "wards": [
        "Phường Cầu Giấy",
        "Phường Hà Đông",
        "Phường Đông Hà",
        "Phường Đống Đa",
        "Phường Thanh Xuân",
        "Phường Hoàn Kiếm",
        "Phường Ba Đình",
        "Phường Long Biên",
        "Phường Tây Hồ",
        "Phường Bến Nghé",
        "Phường Bình Thạnh",
        "Phường Tân Bình",
        "Phường Hải Châu",
        "Phường Trung Hòa",
    ],
    "amenities": list(REAL_AMENITY_NAMES),
}


@pytest.fixture(autouse=True)
def block_all_external_llm_calls(monkeypatch):
    """Hard safety net: block any outgoing HTTP call to external LLM APIs (Gemini/Anthropic)."""
    monkeypatch.setenv("ALLOW_EXTERNAL_FALLBACK", "false")
    get_settings.cache_clear()

    orig_post = httpx.AsyncClient.post
    orig_stream = httpx.AsyncClient.stream

    async def guarded_post(self, url, *args, **kwargs):
        url_str = str(url).lower()
        if any(host in url_str for host in BLOCKED_EXTERNAL_HOSTS):
            raise RuntimeError(f"BLOCKED external LLM HTTP POST to {url_str}")
        return await orig_post(self, url, *args, **kwargs)

    def guarded_stream(self, method, url, *args, **kwargs):
        url_str = str(url).lower()
        if any(host in url_str for host in BLOCKED_EXTERNAL_HOSTS):
            raise RuntimeError(f"BLOCKED external LLM HTTP STREAM to {url_str}")
        return orig_stream(self, method, url, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "post", guarded_post)
    monkeypatch.setattr(httpx.AsyncClient, "stream", guarded_stream)
    yield
    get_settings.cache_clear()


def parse_sse_events(raw_events: list[str]) -> list[tuple[str, dict]]:
    """Parse SSE event strings emitted by _turn_events / chat endpoint."""
    parsed: list[tuple[str, dict]] = []
    for raw in raw_events:
        event_name = ""
        data = {}
        for line in raw.strip().splitlines():
            if line.startswith("event: "):
                event_name = line[7:].strip()
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if event_name:
            parsed.append((event_name, data))
    return parsed


def make_provider_stub(
    name: str = "local",
    *,
    model: str = "qwen3:4b-instruct-2507-q4_K_M",
    result=None,
    extract_error=None,
    chunks: list[str] | None = None,
    compose_error=None,
):
    """Create a deterministic mock LLM provider."""
    stub = Mock(name=name)
    stub.name = name
    stub.model = model
    stub.last_usage = None
    stub.extract = AsyncMock(return_value=result, side_effect=extract_error)

    async def _compose(text, listings):
        if compose_error is not None and not chunks:
            raise compose_error
        for chunk in chunks or []:
            yield chunk
        if compose_error is not None:
            raise compose_error

    stub.compose = _compose
    return stub


def create_inmemory_dbs():
    """Create shared-connection in-memory SQLite engines for Core and AI schemas."""
    core_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ai_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CoreBase.metadata.create_all(core_engine)
    AiBase.metadata.create_all(ai_engine)
    core_factory = sessionmaker(bind=core_engine)
    ai_factory = sessionmaker(bind=ai_engine)
    return core_engine, ai_engine, core_factory, ai_factory


def seed_realistic_rental_db(session: Session) -> dict:
    """Seed realistic Vietnamese provinces, wards, amenities, room types, listings, and profiles."""
    now = datetime.utcnow()

    # Provinces
    provinces = [
        Province(id="p-hn", code="01", name="Thành phố Hà Nội", centroid_lat=21.0285, centroid_lng=105.8542),
        Province(id="p-hcm", code="79", name="Thành phố Hồ Chí Minh", centroid_lat=10.8231, centroid_lng=106.6297),
        Province(id="p-dn", code="48", name="Thành phố Đà Nẵng", centroid_lat=16.0544, centroid_lng=108.2022),
        Province(id="p-qt", code="45", name="Tỉnh Quảng Trị", centroid_lat=16.8163, centroid_lng=107.1003),
        Province(id="p-hp", code="31", name="Thành phố Hải Phòng", centroid_lat=20.8449, centroid_lng=106.6881),
        Province(id="p-ct", code="92", name="Thành phố Cần Thơ", centroid_lat=10.0452, centroid_lng=105.7469),
    ]
    session.add_all(provinces)

    # Wards (including ambiguous ward "Phường Trung Hòa" in 2 provinces & "Hà Đông" vs "Đông Hà")
    wards = [
        Ward(id="w-cg", province_id="p-hn", code="001", name="Phường Cầu Giấy", centroid_lat=21.0362, centroid_lng=105.7906),
        Ward(id="w-dd", province_id="p-hn", code="002", name="Phường Đống Đa", centroid_lat=21.0180, centroid_lng=105.8290),
        Ward(id="w-tx", province_id="p-hn", code="003", name="Phường Thanh Xuân", centroid_lat=20.9950, centroid_lng=105.8050),
        Ward(id="w-hd", province_id="p-hn", code="004", name="Phường Hà Đông", centroid_lat=20.9719, centroid_lng=105.7788),
        Ward(id="w-bd", province_id="p-hn", code="005", name="Phường Ba Đình", centroid_lat=21.0355, centroid_lng=105.8190),
        Ward(id="w-hk", province_id="p-hn", code="006", name="Phường Hoàn Kiếm", centroid_lat=21.0287, centroid_lng=105.8520),
        Ward(id="w-lb", province_id="p-hn", code="007", name="Phường Long Biên", centroid_lat=21.0400, centroid_lng=105.8900),
        Ward(id="w-th-hn", province_id="p-hn", code="008", name="Phường Trung Hòa", centroid_lat=21.0120, centroid_lng=105.7980),
        Ward(id="w-th-hp", province_id="p-hp", code="009", name="Phường Trung Hòa", centroid_lat=20.8500, centroid_lng=106.6900),
        Ward(id="w-dh-qt", province_id="p-qt", code="010", name="Phường Đông Hà", centroid_lat=16.8163, centroid_lng=107.1003),
        Ward(id="w-bn", province_id="p-hcm", code="011", name="Phường Bến Nghé", centroid_lat=10.7798, centroid_lng=106.7000),
        Ward(id="w-bt", province_id="p-hcm", code="012", name="Phường Bình Thạnh", centroid_lat=10.8050, centroid_lng=106.7100),
        Ward(id="w-hc", province_id="p-dn", code="013", name="Phường Hải Châu", centroid_lat=16.0678, centroid_lng=108.2208),
        Ward(id="w-noc", province_id="p-hn", code="014", name="Phường Tây Hồ", centroid_lat=None, centroid_lng=None),
    ]
    session.add_all(wards)

    # Room Types
    room_types = [
        RoomType(id="rt-tro", name="Phòng trọ"),
        RoomType(id="rt-canho", name="Căn hộ"),
        RoomType(id="rt-cc", name="Chung cư"),
        RoomType(id="rt-nha", name="Nhà nguyên căn"),
    ]
    session.add_all(room_types)

    # 14 Real Amenities
    amenity_map = {}
    for idx, name in enumerate(REAL_AMENITY_NAMES, start=1):
        aid = f"am-{idx}"
        amenity_map[name] = aid
        session.add(Amenity(id=aid, name=name))

    def add_listing(
        lid: str,
        *,
        title: str,
        province_id: str,
        ward_id: str,
        price: int,
        area: float,
        room_type_id: str = "rt-tro",
        max_occupants: int | None = 2,
        room_amenities: list[str] | None = None,
        prop_amenities: list[str] | None = None,
        lat: float | None = 21.0362,
        lng: float | None = 105.7906,
        status: str = "PUBLISHED",
        rental_status: str = "AVAILABLE",
        expires_delta_days: int = 30,
        published_delta_hours: int = 0,
        priority_level: int = 0,
        landlord_id: str = "ll-1",
    ):
        pid = f"prop-{lid}"
        rid = f"room-{lid}"
        session.add(
            Property(
                id=pid,
                landlord_id=landlord_id,
                property_type_id="pt-1",
                name=f"Nhà {lid}",
                rental_mode="BY_ROOM",
                province_id=province_id,
                ward_id=ward_id,
                address_line=f"Số 10 đường {lid}",
                latitude=lat,
                longitude=lng,
            )
        )
        session.add(
            Listing(
                id=lid,
                property_id=pid,
                title=title,
                description=f"Mô tả chi tiết {title}",
                status=status,
                priority_level=priority_level,
                expires_at=now + timedelta(days=expires_delta_days),
                published_at=now - timedelta(hours=published_delta_hours),
                created_at=now - timedelta(hours=published_delta_hours),
            )
        )
        session.add(
            Room(
                id=rid,
                property_id=pid,
                room_number=f"P{lid}",
                room_type_id=room_type_id,
                price=price,
                area_m2=area,
                max_occupants=max_occupants,
                rental_status=rental_status,
            )
        )
        for idx, am_name in enumerate(room_amenities or [], start=1):
            session.add(RoomAmenity(id=f"ra-{lid}-{idx}", room_id=rid, amenity_id=amenity_map[am_name]))
        for idx, am_name in enumerate(prop_amenities or [], start=1):
            session.add(PropertyAmenity(id=f"pa-{lid}-{idx}", property_id=pid, amenity_id=amenity_map[am_name]))

    # Active realistic listings across cities
    add_listing(
        "l-cg-1",
        title="Phòng trọ Xuân Thủy Cầu Giấy đầy đủ đồ",
        province_id="p-hn",
        ward_id="w-cg",
        price=2_500_000,
        area=20,
        max_occupants=2,
        room_amenities=["Điều hòa", "Bình nước nóng", "Nhà vệ sinh riêng", "Ban công"],
        prop_amenities=["WiFi", "Chỗ để xe", "Camera an ninh"],
        lat=21.0365,
        lng=105.7910,
        published_delta_hours=1,
    )
    add_listing(
        "l-cg-2",
        title="Phòng trọ Dịch Vọng Cầu Giấy rộng thoáng",
        province_id="p-hn",
        ward_id="w-cg",
        price=3_200_000,
        area=26,
        max_occupants=2,
        room_amenities=["Điều hòa", "Ban công", "Tủ lạnh", "Bếp riêng", "Nhà vệ sinh riêng"],
        prop_amenities=["WiFi", "Chỗ để xe", "Máy giặt dùng chung"],
        lat=21.0358,
        lng=105.7920,
        published_delta_hours=2,
    )
    add_listing(
        "l-dd-1",
        title="Phòng trọ Chùa Láng Đống Đa khép kín",
        province_id="p-hn",
        ward_id="w-dd",
        price=3_400_000,
        area=24,
        max_occupants=2,
        room_amenities=["Điều hòa", "Nhà vệ sinh riêng", "Bếp riêng", "Tủ lạnh"],
        prop_amenities=["WiFi", "Chỗ để xe"],
        lat=21.0200,
        lng=105.8250,
        published_delta_hours=3,
    )
    add_listing(
        "l-dd-2",
        title="Căn hộ mini Đống Đa cho cặp đôi",
        province_id="p-hn",
        ward_id="w-dd",
        price=6_500_000,
        area=40,
        room_type_id="rt-canho",
        max_occupants=2,
        room_amenities=["Điều hòa", "Bếp riêng", "Tủ lạnh", "Nhà vệ sinh riêng", "Ban công"],
        prop_amenities=["WiFi", "Thang máy", "Camera an ninh", "Chỗ để xe"],
        lat=21.0190,
        lng=105.8280,
        published_delta_hours=4,
    )
    add_listing(
        "l-tx-1",
        title="Phòng trọ Thanh Xuân nhỏ gọn ở 1 mình",
        province_id="p-hn",
        ward_id="w-tx",
        price=2_700_000,
        area=18,
        max_occupants=1,
        room_amenities=["Điều hòa", "Ban công", "Cửa sổ"],
        prop_amenities=["Chỗ để xe", "WiFi"],
        lat=20.9960,
        lng=105.8060,
        published_delta_hours=5,
    )
    add_listing(
        "l-hd-1",
        title="Phòng trọ Văn Quán Hà Đông giá sinh viên",
        province_id="p-hn",
        ward_id="w-hd",
        price=2_800_000,
        area=25,
        max_occupants=3,
        room_amenities=["Điều hòa", "Tủ lạnh", "Bếp riêng", "Nhà vệ sinh riêng"],
        prop_amenities=["WiFi", "Máy giặt dùng chung", "Chỗ để xe"],
        lat=20.9720,
        lng=105.7800,
        published_delta_hours=6,
    )
    add_listing(
        "l-bd-1",
        title="Căn hộ Ba Đình cao cấp có thang máy",
        province_id="p-hn",
        ward_id="w-bd",
        price=7_500_000,
        area=45,
        room_type_id="rt-canho",
        max_occupants=3,
        room_amenities=["Điều hòa", "Bếp riêng", "Tủ lạnh", "Giường", "Tủ quần áo"],
        prop_amenities=["Thang máy", "WiFi", "Camera an ninh", "Chỗ để xe"],
        lat=21.0360,
        lng=105.8200,
        published_delta_hours=7,
    )
    add_listing(
        "l-lb-1",
        title="Phòng trọ Long Biên giá rẻ cho sinh viên",
        province_id="p-hn",
        ward_id="w-lb",
        price=2_000_000,
        area=20,
        max_occupants=2,
        room_amenities=["Cửa sổ", "Nhà vệ sinh riêng"],
        prop_amenities=["WiFi", "Chỗ để xe"],
        lat=21.0410,
        lng=105.8910,
        published_delta_hours=8,
    )
    add_listing(
        "l-bt-1",
        title="Căn hộ mini Bình Thạnh TP HCM full nội thất",
        province_id="p-hcm",
        ward_id="w-bt",
        price=5_000_000,
        area=30,
        room_type_id="rt-canho",
        max_occupants=2,
        room_amenities=["Điều hòa", "Tủ lạnh", "Bếp riêng", "Giường"],
        prop_amenities=["WiFi", "Thang máy", "Chỗ để xe"],
        lat=10.8060,
        lng=106.7110,
        published_delta_hours=9,
    )
    add_listing(
        "l-bn-1",
        title="Phòng trọ Bến Nghé Quận 1 gần công ty",
        province_id="p-hcm",
        ward_id="w-bn",
        price=3_800_000,
        area=20,
        max_occupants=2,
        room_amenities=["Điều hòa", "Nhà vệ sinh riêng"],
        prop_amenities=["WiFi", "Camera an ninh"],
        lat=10.7800,
        lng=106.7010,
        published_delta_hours=10,
    )
    add_listing(
        "l-hc-1",
        title="Phòng trọ Hải Châu Đà Nẵng cho nhóm 3 người",
        province_id="p-dn",
        ward_id="w-hc",
        price=4_000_000,
        area=34,
        max_occupants=3,
        room_amenities=["Điều hòa", "Bếp riêng", "Tủ lạnh", "Ban công"],
        prop_amenities=["WiFi", "Chỗ để xe", "Máy giặt dùng chung"],
        lat=16.0680,
        lng=108.2210,
        published_delta_hours=11,
    )
    add_listing(
        "l-dh-qt",
        title="Phòng trọ Đông Hà Quảng Trị yên tĩnh",
        province_id="p-qt",
        ward_id="w-dh-qt",
        price=1_800_000,
        area=22,
        max_occupants=2,
        room_amenities=["Điều hòa", "Nhà vệ sinh riêng"],
        prop_amenities=["WiFi"],
        lat=16.8165,
        lng=107.1010,
        published_delta_hours=12,
    )

    # Inactive / Invalid inventory that MUST NEVER be returned
    add_listing(
        "l-draft",
        title="Phòng nháp không được hiển thị",
        province_id="p-hn",
        ward_id="w-cg",
        price=1_500_000,
        area=25,
        status="DRAFT",
        room_amenities=["Điều hòa"],
    )
    add_listing(
        "l-expired",
        title="Phòng hết hạn không được hiển thị",
        province_id="p-hn",
        ward_id="w-cg",
        price=1_600_000,
        area=25,
        expires_delta_days=-2,
        room_amenities=["Điều hòa"],
    )
    add_listing(
        "l-rented",
        title="Phòng đã cho thuê không được hiển thị",
        province_id="p-hn",
        ward_id="w-cg",
        price=1_700_000,
        area=25,
        rental_status="RENTED",
        room_amenities=["Điều hòa"],
    )

    # Tenant profiles for profile-routing tests
    session.add(
        TenantProfile(
            id="tp-active",
            user_id="user-profile-ok",
            desired_province_id="p-hn",
            desired_ward_id="w-cg",
            price_min=2_000_000,
            price_max=3_500_000,
            area_min=18,
            area_max=30,
            desired_room_type_id="rt-tro",
            tenant_type="STUDENT",
            occupants=2,
            move_in_date=date(2026, 12, 1),
            anchor_label="ĐH Quốc Gia Hà Nội",
            anchor_latitude=21.0370,
            anchor_longitude=105.7820,
            max_distance_km=3,
            price_tolerance_pct=20,
            top_priority="PRICE",
            note="Thích chỗ yên tĩnh",
            allow_personalization=True,
        )
    )
    session.add(
        TenantProfileAmenity(
            id="tpa-1",
            tenant_profile_id="tp-active",
            amenity_id=amenity_map["Điều hòa"],
            is_required=True,
        )
    )
    session.add(
        TenantProfileAmenity(
            id="tpa-2",
            tenant_profile_id="tp-active",
            amenity_id=amenity_map["Ban công"],
            is_required=False,
        )
    )
    session.add(
        TenantProfileWard(
            id="tpw-1",
            tenant_profile_id="tp-active",
            ward_id="w-dd",
        )
    )

    # Profile with personalization OFF
    session.add(
        TenantProfile(
            id="tp-off",
            user_id="user-profile-off",
            desired_province_id="p-hn",
            desired_ward_id="w-cg",
            price_max=3_000_000,
            allow_personalization=False,
        )
    )

    # Empty profile (no criteria set)
    session.add(
        TenantProfile(
            id="tp-empty",
            user_id="user-profile-empty",
            allow_personalization=True,
        )
    )

    session.commit()
    return {
        "amenity_map": amenity_map,
        "catalogue": {
            "provinces": [p.name for p in provinces],
            "wards": [w.name for w in wards],
            "amenities": list(REAL_AMENITY_NAMES),
        },
    }
