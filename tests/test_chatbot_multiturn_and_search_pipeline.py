"""Kiểm thử hội thoại nhiều lượt (multi-turn), tìm kiếm SQL, nới lỏng dung sai 7 tầng & luồng SSE / ngoại lệ runtime."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.api.chat import ChatRequest, Turn, _turn_events, chat
from app.chat.llm.base import ProviderError
from app.chat.llm.breaker import CircuitBreaker
from app.chat.llm.gateway import AllProvidersFailed, Gateway
from app.chat.llm.rules_provider import RulesProvider
from app.chat.schemas import ExtractResult, SearchFilters
from app.chat.search import FilterResolutionError, expanded_bounds, fetch_listing_cards
from app.chat.service import (
    OUT_OF_SCOPE_REPLY,
    apply_profile_defaults_to_filters,
    merge_filters,
)
from app.chat.suggestions import compose_suggestions, search_with_suggestions
from app.core.security import AuthenticatedUser
from app.db.models_ai import AiChatLease, AiMessage
from app.db.models_core import Listing, Property, Room, RoomAmenity, Ward
from app.observability.call_logger import CallLogger
from tests.conftest import (
    create_inmemory_dbs,
    make_provider_stub,
    parse_sse_events,
    seed_realistic_rental_db,
)


# =============================================================================
# 1. MULTI-TURN MERGE_FILTERS & PROFILE DEFAULTS PRECEDENCE
# =============================================================================


def test_merge_filters_additive_overwrite_and_conflict_resolution():
    prev = {
        "province": "Thành phố Hà Nội",
        "ward_or_area": "Cầu Giấy",
        "price_min": 3_000_000,
        "price_max": 4_000_000,
        "area_min": 18.0,
        "area_max": 22.0,
        "amenities": ["Điều hòa"],
        "occupants": 1,
    }

    # 1. Thêm tiện ích mới -> cộng dồn không trùng lặp
    m1 = merge_filters(SearchFilters(amenities=["Điều hòa", "Ban công"]), prev)
    assert m1.amenities == ["Điều hòa", "Ban công"]
    assert m1.ward_or_area == "Cầu Giấy"

    # 2. Hạ price_max xuống dưới price_min cũ (3-4tr -> dưới 2.5tr) -> tự xóa price_min cũ!
    m2 = merge_filters(SearchFilters(price_max=2_500_000), prev)
    assert m2.price_min is None
    assert m2.price_max == 2_500_000

    # 3. Nâng price_min lên trên price_max cũ (3-4tr -> trên 5tr) -> tự xóa price_max cũ!
    m3 = merge_filters(SearchFilters(price_min=5_000_000), prev)
    assert m3.price_min == 5_000_000
    assert m3.price_max is None

    # 4. Nâng area_min lên trên area_max cũ (18-22m2 -> trên 30m2) -> tự xóa area_max cũ!
    m4 = merge_filters(SearchFilters(area_min=30.0), prev)
    assert m4.area_min == 30.0
    assert m4.area_max is None

    # 5. Hạ area_max xuống dưới area_min cũ -> tự xóa area_min cũ!
    m5 = merge_filters(SearchFilters(area_max=15.0), prev)
    assert m5.area_min is None
    assert m5.area_max == 15.0

    # 6. Đổi sang tỉnh mới mà không nêu phường -> xóa ward_or_area cũ thuộc Hà Nội
    m6 = merge_filters(SearchFilters(province="Thành phố Đà Nẵng"), prev)
    assert m6.province == "Thành phố Đà Nẵng"
    assert m6.ward_or_area is None

    # 7. Đổi sang tỉnh mới KÈM phường mới -> giữ cả hai
    m7 = merge_filters(SearchFilters(province="Thành phố Đà Nẵng", ward_or_area="Hải Châu"), prev)
    assert m7.province == "Thành phố Đà Nẵng"
    assert m7.ward_or_area == "Hải Châu"

    # 8. Đổi sang phường mới cùng tỉnh -> giữ tỉnh cũ, thay phường mới
    m8 = merge_filters(SearchFilters(ward_or_area="Đống Đa"), prev)
    assert m8.province == "Thành phố Hà Nội"
    assert m8.ward_or_area == "Đống Đa"


def test_apply_profile_defaults_never_overrides_explicit_and_never_creates_range_conflict():
    profile = {
        "ward_id": "w-cg",
        "price_min": 3_000_000,
        "price_max": 5_000_000,
        "area_min": 25.0,
        "area_max": 35.0,
        "occupants": 2,
    }
    ward_lookup = {"w-cg": "Phường Cầu Giấy"}

    # Khách nói rõ dưới 2.5tr ở Đống Đa -> KHÔNG lấy price_min=3tr hay ward Cầu Giấy từ profile!
    explicit = SearchFilters(ward_or_area="Đống Đa", price_max=2_500_000, area_max=20.0)
    merged = apply_profile_defaults_to_filters(explicit, profile, ward_lookup)
    assert merged.ward_or_area == "Đống Đa"
    assert merged.price_min is None  # tránh 3tr > 2.5tr
    assert merged.price_max == 2_500_000
    assert merged.area_min is None   # tránh 25 > 20
    assert merged.area_max == 20.0
    assert merged.occupants == 2     # trường khách chưa nói -> điền từ profile


# =============================================================================
# 2. TÌM KIẾM INVENTORY & NỚI LỎNG DUNG SAI ĐA TẦNG (TOLERANCE MATRIX)
# =============================================================================


def test_inventory_excludes_draft_expired_rented_and_older_listings_and_prevents_amenity_leak():
    core_engine, ai_engine, core_factory, _ = create_inmemory_dbs()
    try:
        with core_factory() as session:
            seed_realistic_rental_db(session)
            now = datetime.utcnow()

            # Thêm 1 property có 2 phòng: phòng 1 có Điều hòa (giá 3.5tr), phòng 2 có Ban công (giá 2.4tr)
            # Khi tìm Điều hòa + Ban công -> KHÔNG được gộp tiện ích giữa 2 phòng khác nhau!
            session.add(
                Property(
                    id="prop-multi",
                    landlord_id="ll-2",
                    property_type_id="pt-1",
                    name="Nhà nhiều phòng",
                    rental_mode="BY_ROOM",
                    province_id="p-hn",
                    ward_id="w-hk",
                    address_line="10 Hàng Bông",
                    latitude=21.0287,
                    longitude=105.8520,
                )
            )
            session.add(
                Listing(
                    id="l-multi-old",
                    property_id="prop-multi",
                    title="Tin cũ hơn",
                    status="PUBLISHED",
                    expires_at=now + timedelta(days=30),
                    published_at=now - timedelta(days=2),
                    created_at=now - timedelta(days=2),
                )
            )
            session.add(
                Listing(
                    id="l-multi-new",
                    property_id="prop-multi",
                    title="Tin mới nhất",
                    status="PUBLISHED",
                    expires_at=now + timedelta(days=30),
                    published_at=now - timedelta(hours=1),
                    created_at=now - timedelta(hours=1),
                )
            )
            session.add(Room(id="r-m1", property_id="prop-multi", room_number="101", room_type_id="rt-tro", price=3_500_000, area_m2=22, max_occupants=2, rental_status="AVAILABLE"))
            session.add(Room(id="r-m2", property_id="prop-multi", room_number="102", room_type_id="rt-tro", price=2_400_000, area_m2=18, max_occupants=2, rental_status="AVAILABLE"))
            session.add(RoomAmenity(id="ra-m1", room_id="r-m1", amenity_id="am-7"))  # Điều hòa
            session.add(RoomAmenity(id="ra-m2", room_id="r-m2", amenity_id="am-1"))  # Ban công
            session.commit()

            # 1. Ở Cầu Giấy không bao giờ xuất hiện l-draft, l-expired, l-rented
            cg_items = search_with_suggestions(session, SearchFilters(ward_or_area="Cầu Giấy", price_max=3_500_000))
            cg_ids = {item["id"] for item in cg_items}
            assert cg_ids == {"l-cg-1", "l-cg-2"}

            # 2. Ở Hoàn Kiếm chỉ lấy tin mới nhất (l-multi-new) và chọn phòng rẻ nhất (r-m2: 2.4tr)
            hk_items = search_with_suggestions(session, SearchFilters(ward_or_area="Hoàn Kiếm", price_max=4_000_000))
            assert len(hk_items) == 1
            assert hk_items[0]["id"] == "l-multi-new"
            assert hk_items[0]["matched_room_id"] == "r-m2"

            # 3. Yêu cầu CẢ Điều hòa lẫn Ban công ở Hoàn Kiếm -> không phòng nào có cả 2 -> không leak chéo!
            both_items = search_with_suggestions(
                session,
                SearchFilters(ward_or_area="Hoàn Kiếm", amenities=["Điều hòa", "Ban công"], price_max=4_000_000),
            )
            assert all(item["id"] != "l-multi-new" for item in both_items)
    finally:
        core_engine.dispose()
        ai_engine.dispose()


def test_tolerance_relaxation_stages_hard_caps_and_immutability():
    core_engine, ai_engine, core_factory, _ = create_inmemory_dbs()
    try:
        with core_factory() as session:
            seed_realistic_rental_db(session)

            # Kiểm tra công thức trần cứng dung sai
            assert expanded_bounds(2_000_000, 8_000_000, 20, 500_000) == (1_600_000, 8_500_000)
            assert expanded_bounds(20, 50, 15, 5) == (17, 55)

            # 1. Nới lỏng GIÁ ở Cầu Giấy: khách tìm <= 2.3tr (l-cg-1 giá 2.5tr, lệch 200k <= 10%/20%)
            f_price = SearchFilters(ward_or_area="Cầu Giấy", price_max=2_300_000)
            snapshot = f_price.model_dump()
            items_p = search_with_suggestions(session, f_price)
            assert f_price.model_dump() == snapshot  # Bất biến bộ lọc gốc!
            assert items_p[0]["id"] == "l-cg-1"
            assert items_p[0]["match_type"] == "PRICE"
            assert "vượt ngân sách 200,000" in items_p[0]["differences"][0]

            # 2. Vượt quá giới hạn nới lỏng giá (tìm <= 1tr ở Cầu Giấy, không phòng nào trong vòng 1.2tr) -> rỗng
            items_none = search_with_suggestions(session, SearchFilters(ward_or_area="Cầu Giấy", price_max=1_000_000))
            assert items_none == []
            msg_none = compose_suggestions(items_none, SearchFilters(ward_or_area="Cầu Giấy", price_max=1_000_000, amenities=["Điều hòa"]))
            assert "1.2 triệu" in msg_none
            assert "Điều hòa" in msg_none

            # 3. Nới lỏng KHU VỰC LÂN CẬN trong bán kính 5km cùng tỉnh:
            # Tìm phòng giá <= 2.7tr, diện tích <= 19m2 ở Cầu Giấy -> l-tx-1 ở Thanh Xuân (~4.8km) khớp chính xác giá/diện tích
            items_nearby = search_with_suggestions(
                session,
                SearchFilters(ward_or_area="Cầu Giấy", price_max=2_700_000, area_max=19, amenities=["Điều hòa"]),
                radius_km=5,
            )
            assert len(items_nearby) >= 1
            assert items_nearby[0]["match_type"] in ("NEARBY", "AREA")

            # 4. Phường không có tọa độ centroid (Tây Hồ) -> KHÔNG tự đoán tọa độ, chỉ nới giá/diện tích hoặc trả rỗng
            items_no_centroid = search_with_suggestions(
                session,
                SearchFilters(ward_or_area="Tây Hồ", price_max=3_000_000),
            )
            assert items_no_centroid == []

            # 5. Bảo toàn ràng buộc cứng (room_type="Căn hộ", occupants=4): KHÔNG bao giờ nới lỏng loại phòng / sức chứa!
            items_hard = search_with_suggestions(
                session,
                SearchFilters(ward_or_area="Cầu Giấy", room_type="Căn hộ", price_max=3_500_000),
            )
            assert all(item["room_type_name"] == "Căn hộ" for item in items_hard)

            # 6. Tiện ích không tồn tại trong DB -> FilterResolutionError ngay lập tức
            with pytest.raises(FilterResolutionError, match="hồ bơi"):
                search_with_suggestions(session, SearchFilters(ward_or_area="Cầu Giấy", amenities=["hồ bơi"]))
    finally:
        core_engine.dispose()
        ai_engine.dispose()


# =============================================================================
# 3. LUỒNG SSE END-TO-END & XỬ LÝ NGOẠI LỆ TRONG _turn_events / chat()
# =============================================================================


@pytest.mark.asyncio
async def test_sse_full_lifecycle_exact_relaxed_fallback_and_exceptions():
    core_engine, ai_engine, core_factory, ai_factory = create_inmemory_dbs()
    try:
        with core_factory() as session:
            seeded = seed_realistic_rental_db(session)
            catalogue = seeded["catalogue"]

        with patch("app.api.chat.core_session_factory", core_factory), patch(
            "app.api.chat.ai_session_factory", ai_factory
        ), patch("app.core.rate_limit.ai_session_factory", ai_factory):
            # --- Case A: Exact match -> gọi local compose stream thành công ---
            local_ok = make_provider_stub(
                "local",
                result=ExtractResult(intent="SEARCH", filters=SearchFilters(ward_or_area="Cầu Giấy", price_max=3_000_000)),
                chunks=["Mình tìm được ", "1 phòng ở Cầu Giấy giá 2,5 triệu."],
            )
            gw_ok = Gateway([local_ok, RulesProvider()], {"local": CircuitBreaker()}, CallLogger())
            turn_a = Turn("req-a", "conv-1", "u-1", "phòng Cầu Giấy dưới 3tr")
            raw_a = [ev async for ev in _turn_events(turn_a, gw_ok, None, catalogue, {}, {})]
            ev_a = parse_sse_events(raw_a)
            assert turn_a.status == "OK"
            assert [name for name, _ in ev_a] == ["listings", "token", "token"]
            assert ev_a[0][1]["items"][0]["id"] == "l-cg-1"
            assert "".join(turn_a.parts) == "Mình tìm được 1 phòng ở Cầu Giấy giá 2,5 triệu."

            # --- Case B: Relaxed match -> dùng compose_suggestions xác định, KHÔNG gọi LLM compose ---
            local_rel = make_provider_stub(
                "local",
                result=ExtractResult(intent="SEARCH", filters=SearchFilters(ward_or_area="Cầu Giấy", price_max=2_300_000)),
                compose_error=AssertionError("LLM compose must NOT run on relaxed matches"),
            )
            gw_rel = Gateway([local_rel, RulesProvider()], {"local": CircuitBreaker()}, CallLogger())
            turn_b = Turn("req-b", "conv-1", "u-1", "phòng Cầu Giấy dưới 2.3tr")
            raw_b = [ev async for ev in _turn_events(turn_b, gw_rel, None, catalogue, {}, {})]
            ev_b = parse_sse_events(raw_b)
            assert turn_b.status == "OK"
            assert ev_b[0][1]["items"][0]["match_type"] == "PRICE"
            assert "Chưa có phòng khớp toàn bộ yêu cầu" in "".join(turn_b.parts)

            # --- Case C: Local compose lỗi giữa chừng (sau khi đã gửi 1 token) -> phát event retry, không nối model khác ---
            local_mid = make_provider_stub(
                "local",
                result=ExtractResult(intent="SEARCH", filters=SearchFilters(ward_or_area="Cầu Giấy", price_max=3_000_000)),
                chunks=["Đang trả lời dở..."],
                compose_error=ProviderError("stream broken"),
            )
            gw_mid = Gateway([local_mid, RulesProvider()], {"local": CircuitBreaker()}, CallLogger())
            turn_c = Turn("req-c", "conv-1", "u-1", "phòng Cầu Giấy dưới 3tr")
            raw_c = [ev async for ev in _turn_events(turn_c, gw_mid, None, catalogue, {}, {})]
            ev_c = parse_sse_events(raw_c)
            assert any(name == "retry" and data["reason"] == "provider_failed_mid_stream" for name, data in ev_c)

            # --- Case D: Local compose trả rỗng (0 token) -> tự động fallback sang RulesProvider compose_template ---
            local_empty = make_provider_stub(
                "local",
                result=ExtractResult(intent="SEARCH", filters=SearchFilters(ward_or_area="Cầu Giấy", price_max=3_000_000)),
                chunks=[],
            )
            gw_empty = Gateway([local_empty, RulesProvider()], {"local": CircuitBreaker()}, CallLogger())
            turn_d = Turn("req-d", "conv-1", "u-1", "phòng Cầu Giấy dưới 3tr")
            raw_d = [ev async for ev in _turn_events(turn_d, gw_empty, None, catalogue, {}, {})]
            assert turn_d.status == "OK"
            assert "Phòng trọ Xuân Thủy" in "".join(turn_d.parts)

            # --- Case E: Local OUT_OF_SCOPE không có câu trả lời -> dùng OUT_OF_SCOPE_REPLY mặc định ---
            local_oos = make_provider_stub("local", result=ExtractResult(intent="OUT_OF_SCOPE"))
            gw_oos = Gateway([local_oos, RulesProvider()], {"local": CircuitBreaker()}, CallLogger())
            turn_e = Turn("req-e", "conv-1", "u-1", "giá vàng hôm nay")
            raw_e = [ev async for ev in _turn_events(turn_e, gw_oos, {"ward_or_area": "Cầu Giấy"}, catalogue, {}, {})]
            assert turn_e.status == "OUT_OF_SCOPE"
            assert turn_e.filters == {"ward_or_area": "Cầu Giấy"}  # Giữ nguyên ngữ cảnh cũ!
            assert "".join(turn_e.parts) == OUT_OF_SCOPE_REPLY
    finally:
        core_engine.dispose()
        ai_engine.dispose()


@pytest.mark.asyncio
async def test_chat_endpoint_handles_filter_resolution_error_all_providers_failed_and_cleanup():
    core_engine, ai_engine, core_factory, ai_factory = create_inmemory_dbs()
    try:
        with core_factory() as session:
            seed_realistic_rental_db(session)

        user = AuthenticatedUser(id="u-sse-1", scopes=frozenset({"ROLE_TENANT"}), auth_version=1)

        with patch("app.api.chat.core_session_factory", core_factory), patch(
            "app.api.chat.ai_session_factory", ai_factory
        ), patch("app.core.rate_limit.ai_session_factory", ai_factory):
            # 1. FilterResolutionError (phường không tồn tại "Sao Hỏa") -> trả CLARIFY lịch sự & giải phóng lease
            local_mars = make_provider_stub(
                "local",
                result=ExtractResult(intent="SEARCH", filters=SearchFilters(ward_or_area="Sao Hỏa")),
            )
            gw_mars = Gateway([local_mars, RulesProvider()], {"local": CircuitBreaker()}, CallLogger())
            resp1 = await chat(ChatRequest(message="tìm phòng ở Sao Hỏa"), user=user, gateway=gw_mars)
            chunks1 = [chunk async for chunk in resp1.body_iterator]
            events1 = parse_sse_events(chunks1)
            done1 = [d for n, d in events1 if n == "done"][0]
            tokens1 = "".join(d["text"] for n, d in events1 if n == "token")
            assert done1["status"] == "CLARIFY"
            assert "chưa xác định được khu vực" in tokens1.lower()

            # 2. AllProvidersFailed -> trả done status=FAILED, không crash SSE, vẫn lưu log và release lease
            gw_broken = Gateway(
                [
                    make_provider_stub("local", extract_error=ProviderError("down")),
                    make_provider_stub("rules", extract_error=RuntimeError("boom")),
                ],
                {"local": CircuitBreaker()},
                CallLogger(),
            )
            resp2 = await chat(ChatRequest(message="phòng Cầu Giấy"), user=user, gateway=gw_broken)
            chunks2 = [chunk async for chunk in resp2.body_iterator]
            events2 = parse_sse_events(chunks2)
            done2 = [d for n, d in events2 if n == "done"][0]
            assert done2["status"] == "FAILED"

            # Kiểm tra DB AI: lease đã được giải phóng hoàn toàn & tin nhắn đã lưu
            with ai_factory() as session:
                remaining_leases = session.query(AiChatLease).all()
                assert remaining_leases == []
                messages = session.query(AiMessage).all()
                assert len(messages) == 4  # 2 lượt x (USER + ASSISTANT)
    finally:
        core_engine.dispose()
        ai_engine.dispose()
