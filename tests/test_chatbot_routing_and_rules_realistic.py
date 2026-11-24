"""Kiểm thử toàn diện routing Small Talk, Profile, Locations, Amenities & RulesProvider với ngữ liệu thực tế Việt Nam."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.chat.llm.rules_provider import compose_template, extract_filters_by_rules
from app.chat.locations import apply_explicit_locations, canonical_province_needle, named_place, named_ward
from app.chat.profile import detect_profile_action, profile_reply
from app.chat.schemas import ExtractResult, SearchFilters
from app.chat.search import FilterResolutionError, resolve_amenity_ids, resolve_locations
from app.chat.service import apply_explicit_filters
from app.chat.small_talk import small_talk_reply
from tests.conftest import SAMPLE_CATALOGUE, create_inmemory_dbs, seed_realistic_rental_db


# =============================================================================
# 1. SMALL TALK VS CÂU CHÀO/CẢM ƠN CÓ LẪN NHU CẦU TÌM PHÒNG (30+ cases)
# =============================================================================


@pytest.mark.parametrize(
    "text",
    [
        "alo",
        "A lo!!!",
        "hello",
        "helo ban oi",
        "hi",
        "Hey bạn",
        "chào",
        "Xin chào bạn nhé",
        "Chào em ạ",
        "chao anh oi",
        "chao chi nha",
        "bạn ơi",
        "có ai không?",
        "ban co do khong",
        "cảm ơn",
        "cảm ơn bạn nhiều nhé",
        "thanks bạn nha",
        "Thank you!",
        "dạ cảm ơn ạ",
        "tạm biệt",
        "bye bye",
        "bai bai bạn nhé",
        "hẹn gặp lại nha",
        "bạn là ai?",
        "bạn tên gì",
        "bạn làm được gì?",
        "ban giup duoc gi",
    ],
)
def test_pure_small_talk_gets_instant_friendly_reply(text):
    reply = small_talk_reply(text)
    assert reply is not None
    assert len(reply) > 10


@pytest.mark.parametrize(
    "text",
    [
        "chào bạn, tìm phòng trọ dưới 3 triệu ở Cầu Giấy giúp mình",
        "alo có phòng nào ở Hà Đông không bạn",
        "hi em ơi tìm căn hộ 2 phòng ở Bình Thạnh",
        "xin chào, mình là sinh viên cần thuê trọ tầm 2tr5",
        "cảm ơn bạn, nhưng tìm thêm phòng có điều hòa nhé",
        "bạn ơi phòng dưới 3tr có ban công không",
        "hello tư vấn phòng theo hồ sơ của tôi đi",
        "chào bạn thời tiết Hà Nội hôm nay sao nhỉ",
        "tìm phòng trọ",
        "có phòng không bạn",
        "???",
        "😂😂😂",
    ],
)
def test_small_talk_never_swallows_search_profile_or_other_messages(text):
    assert small_talk_reply(text) is None


# =============================================================================
# 2. ĐỊNH TUYẾN HỒ SƠ TENANT (PROFILE ROUTING & EDGE CASES)
# =============================================================================


@pytest.mark.parametrize(
    "text,prev_intent,expected_action",
    [
        # Xem hồ sơ
        ("xem hồ sơ", None, "SHOW"),
        ("hiển thị profile!", None, "SHOW"),
        ("hồ sơ của tôi gồm những gì?", None, "SHOW"),
        ("tiêu chí của mình hiện tại như thế nào", None, "SHOW"),
        ("nhu cầu của tôi có những điều kiện gì", None, "SHOW"),
        # Gợi ý theo hồ sơ
        ("tư vấn phòng giúp mình", None, "RECOMMEND"),
        ("gợi ý phòng trọ phù hợp cho tôi nhé", None, "RECOMMEND"),
        ("tìm phòng cho mình với", None, "RECOMMEND"),
        ("bạn có biết phòng nào phù hợp không ạ", None, "RECOMMEND"),
        ("gợi ý phòng dựa trên hồ sơ của tôi", None, "RECOMMEND"),
        ("tìm phòng theo nhu cầu hiện tại", None, "RECOMMEND"),
        # Vừa xem vừa gợi ý
        ("xem hồ sơ của tôi và gợi ý phòng phù hợp", None, "BOTH"),
        ("hồ sơ của mình có những gì, tư vấn phòng giúp mình luôn nhé", None, "BOTH"),
        # Đồng ý nối tiếp sau PROFILE_SHOW
        ("có", "PROFILE_SHOW", "RECOMMEND"),
        ("vâng", "PROFILE_SHOW", "RECOMMEND"),
        ("ok nhé", "PROFILE_SHOW", "RECOMMEND"),
        ("oke", "PROFILE_SHOW", "RECOMMEND"),
        ("đồng ý", "PROFILE_SHOW", "RECOMMEND"),
        ("gợi ý đi", "PROFILE_SHOW", "RECOMMEND"),
        ("có gợi ý đi nhé", "PROFILE_SHOW", "RECOMMEND"),
        # Không kích hoạt consent khi lượt trước không phải PROFILE_SHOW
        ("ok", "SEARCH", None),
        ("có", None, None),
        ("được", "CLARIFY", None),
        # Từ chối dùng hồ sơ -> phải đi tìm bình thường
        ("không dùng hồ sơ của tôi, tìm phòng ở Đống Đa dưới 4 triệu", None, None),
        ("bỏ qua profile của mình đi, tìm phòng Hà Đông", None, None),
        ("không theo nhu cầu hiện tại đâu", None, None),
        # Có tiêu chí cụ thể -> KHÔNG được bị nuốt thành generic RECOMMEND
        ("tìm phòng ở Cầu Giấy giúp mình", None, None),
        ("gợi ý phòng dưới 3 triệu", None, None),
        ("tư vấn phòng có điều hòa ở Đống Đa", None, None),
        ("tìm phòng cho 2 người", None, None),
        ("tìm phòng 25m2", None, None),
    ],
)
def test_profile_action_detection_matrix(text, prev_intent, expected_action):
    assert detect_profile_action(text, prev_intent) == expected_action


def test_profile_reply_covers_show_recommend_both_missing_disabled_and_empty():
    core_engine, ai_engine, core_factory, ai_factory = create_inmemory_dbs()
    try:
        with patch("app.db.session.ai_session_factory", ai_factory), core_factory() as session:
            seed_realistic_rental_db(session)

            # 1. Chưa có hồ sơ
            missing = profile_reply(session, "user-no-profile", "SHOW", request_id="r1")
            assert missing.status == "CLARIFY"
            assert "chưa lưu hồ sơ" in missing.text.lower()

            # 2. Tắt cá nhân hóa -> SHOW vẫn xem được, RECOMMEND bị chặn lịch sự
            show_off = profile_reply(session, "user-profile-off", "SHOW", request_id="r2")
            assert show_off.status == "OK"
            assert "Đang tắt" in show_off.text
            assert "gợi ý phòng theo hồ sơ" not in show_off.text

            rec_off = profile_reply(session, "user-profile-off", "RECOMMEND", request_id="r3")
            assert rec_off.status == "CLARIFY"
            assert "tắt cá nhân hóa" in rec_off.text.lower()

            # 3. Hồ sơ trống chưa đặt tiêu chí -> RECOMMEND hỏi lại
            rec_empty = profile_reply(session, "user-profile-empty", "RECOMMEND", request_id="r4")
            assert rec_empty.status == "CLARIFY"
            assert "chưa có tiêu chí" in rec_empty.text.lower()

            # 4. Hồ sơ đầy đủ -> SHOW hiển thị đủ trường & hỏi xác nhận gợi ý
            show_ok = profile_reply(session, "user-profile-ok", "SHOW", request_id="r5")
            assert show_ok.status == "OK"
            assert "Phường Cầu Giấy" in show_ok.text
            assert "ĐH Quốc Gia Hà Nội" in show_ok.text
            assert "Điều hòa" in show_ok.text
            assert "Ban công" in show_ok.text
            assert "gợi ý phòng theo hồ sơ" in show_ok.text

            # 5. BOTH -> trả cả tóm tắt hồ sơ lẫn danh sách phòng gợi ý thật từ DB
            both_ok = profile_reply(session, "user-profile-ok", "BOTH", request_id="r6")
            assert both_ok.status == "OK"
            assert both_ok.intent == "PROFILE_RECOMMEND"
            assert "Hồ sơ tìm trọ đang lưu" in both_ok.text
            assert len(both_ok.items) >= 1
    finally:
        core_engine.dispose()
        ai_engine.dispose()


# =============================================================================
# 3. ĐỊA DANH, VIẾT TẮT TỈNH THÀNH & PHƯỜNG TRÙNG TÊN (LOCATION EDGE CASES)
# =============================================================================


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("TP HCM", "ho chi minh"),
        ("tphcm", "ho chi minh"),
        ("Sài Gòn", "ho chi minh"),
        ("sg", "ho chi minh"),
        ("HN", "ha noi"),
        ("TP HN", "ha noi"),
        ("Thành phố Hà Nội", "ha noi"),
        ("dn", "da nang"),
        ("hp", "hai phong"),
        ("ct", "can tho"),
        ("Tỉnh Quảng Trị", "quang tri"),
    ],
)
def test_canonical_province_needle_normalizes_all_aliases(raw, expected):
    assert canonical_province_needle(raw) == expected


def test_ha_dong_ha_noi_never_confused_with_dong_ha_quang_tri():
    """Lỗi kinh điển ghép từ 'Hà Đông, Hà Nội' thành 'Đông Hà' không bao giờ được xảy ra."""
    text = "Tìm phòng ở Hà Đông, Hà Nội giá dưới 3 triệu"
    prov = named_place(text, SAMPLE_CATALOGUE["provinces"])
    ward = named_ward(text, SAMPLE_CATALOGUE["wards"], prov)
    assert prov == "Thành phố Hà Nội"
    assert ward == "Hà Đông"

    text2 = "Tìm phòng ở Đông Hà, Quảng Trị"
    prov2 = named_place(text2, SAMPLE_CATALOGUE["provinces"])
    ward2 = named_ward(text2, SAMPLE_CATALOGUE["wards"], prov2)
    assert prov2 == "Tỉnh Quảng Trị"
    assert ward2 == "Đông Hà"


def test_apply_explicit_locations_recovers_province_and_ward_from_slang():
    res = ExtractResult(intent="SEARCH", filters=SearchFilters())
    apply_explicit_locations(res, "can ho o binh thanh tphcm tam 5tr", SAMPLE_CATALOGUE)
    assert res.filters.province == "Thành phố Hồ Chí Minh"
    assert res.filters.ward_or_area == "Bình Thạnh"

    res2 = ExtractResult(intent="SEARCH", filters=SearchFilters())
    apply_explicit_locations(res2, "tìm trọ ở Bến Nghé Sài Gòn dưới 4 củ", SAMPLE_CATALOGUE)
    assert res2.filters.province == "Thành phố Hồ Chí Minh"
    assert res2.filters.ward_or_area == "Bến Nghé"


def test_db_location_resolution_handles_ambiguity_unknown_and_disambiguation():
    core_engine, ai_engine, core_factory, _ = create_inmemory_dbs()
    try:
        with core_factory() as session:
            seed_realistic_rental_db(session)

            # Phường Trung Hòa tồn tại ở cả Hà Nội và Hải Phòng -> không nêu tỉnh phải báo lỗi yêu cầu làm rõ
            with pytest.raises(FilterResolutionError, match="nhiều tỉnh"):
                resolve_locations(session, "Trung Hòa", None)

            # Khi nêu rõ tỉnh -> giải quyết chính xác 1 phường
            wids, pids = resolve_locations(session, "Trung Hòa", "HN")
            assert wids == ["w-th-hn"]
            assert pids == ["p-hn"]

            wids_hp, pids_hp = resolve_locations(session, "Trung Hòa", "Hải Phòng")
            assert wids_hp == ["w-th-hp"]
            assert pids_hp == ["p-hp"]

            # Khu vực không tồn tại / vô lý -> FilterResolutionError lịch sự
            for unknown_area in ("Sao Hỏa", "Tokyo", "Xã Không Tồn Tại", "Trung tâm"):
                with pytest.raises(FilterResolutionError):
                    resolve_locations(session, unknown_area, None)

            # Tỉnh không tồn tại -> FilterResolutionError
            with pytest.raises(FilterResolutionError, match="tỉnh hoặc thành phố"):
                resolve_locations(session, None, "Tỉnh Không Có Thật")

            # Chỉ nêu tỉnh trong ward_or_area ("Sài Gòn") -> giải quyết về province_ids
            wids_sg, pids_sg = resolve_locations(session, "Sài Gòn", None)
            assert wids_sg == []
            assert pids_sg == ["p-hcm"]
    finally:
        core_engine.dispose()
        ai_engine.dispose()


# =============================================================================
# 4. ĐỒNG NGHĨA TIỆN ÍCH, PHỦ ĐỊNH & BẪY "TỦ LẠNH vs BÌNH NƯỚC NÓNG"
# =============================================================================


def test_amenity_synonyms_and_unknown_preferences():
    core_engine, ai_engine, core_factory, _ = create_inmemory_dbs()
    try:
        with core_factory() as session:
            seed_realistic_rental_db(session)

            synonym_pairs = [
                ("máy lạnh", "Điều hòa"),
                ("đh", "Điều hòa"),
                ("ac", "Điều hòa"),
                ("nóng lạnh", "Bình nước nóng"),
                ("bình nóng lạnh", "Bình nước nóng"),
                ("nl", "Bình nước nóng"),
                ("wc riêng", "Nhà vệ sinh riêng"),
                ("wc khép kín", "Nhà vệ sinh riêng"),
                ("toilet riêng", "Nhà vệ sinh riêng"),
                ("vệ sinh khép kín", "Nhà vệ sinh riêng"),
                ("nvs riêng", "Nhà vệ sinh riêng"),
                ("bãi giữ xe", "Chỗ để xe"),
                ("chỗ gửi xe", "Chỗ để xe"),
                ("để xe máy", "Chỗ để xe"),
                ("nhà xe", "Chỗ để xe"),
                ("internet", "WiFi"),
                ("mạng", "WiFi"),
                ("bếp", "Bếp riêng"),
                ("nấu ăn", "Bếp riêng"),
            ]
            for slang, _canonical in synonym_pairs:
                matched, unknown = resolve_amenity_ids(session, [slang])
                assert unknown == [], f"Failed to resolve synonym: {slang}"
                assert len(matched) == 1

            # Các yêu cầu đời thực không có trong bảng amenities phải báo unknown rõ ràng
            for non_db in ("hồ bơi vô cực", "nuôi thú cưng", "không chung chủ", "giờ giấc tự do", "sân golf"):
                matched, unknown = resolve_amenity_ids(session, [non_db])
                assert matched == []
                assert unknown == [non_db]
    finally:
        core_engine.dispose()
        ai_engine.dispose()


def test_apply_explicit_filters_fixes_price_direction_negation_fridge_and_occupants():
    # 1. Giá đơn bị nhét nhầm vào price_min -> tự đảo về price_max
    r1 = ExtractResult(intent="SEARCH", filters=SearchFilters(price_min=2_500_000))
    apply_explicit_filters(r1, "phòng tầm 2tr5 ở Cầu Giấy", SAMPLE_CATALOGUE)
    assert r1.filters.price_min is None
    assert r1.filters.price_max == 2_500_000

    # Nhưng nếu có từ chỉ hướng tăng ("từ 6 triệu trở lên") -> giữ nguyên price_min
    r2 = ExtractResult(intent="SEARCH", filters=SearchFilters(price_min=6_000_000))
    apply_explicit_filters(r2, "căn hộ giá từ 6 triệu trở lên", SAMPLE_CATALOGUE)
    assert r2.filters.price_min == 6_000_000
    assert r2.filters.price_max is None

    # 2. Phủ định tiện ích ("không cần ban công, bỏ điều hòa, chỉ cần wifi") -> không tự thêm tiện ích bị phủ định
    r3 = ExtractResult(intent="SEARCH", filters=SearchFilters(amenities=[]))
    apply_explicit_filters(r3, "không cần ban công, bỏ điều hòa, chỉ cần wifi và tủ lạnh", SAMPLE_CATALOGUE)
    assert "Ban công" not in r3.filters.amenities
    assert "Điều hòa" not in r3.filters.amenities
    assert "WiFi" in r3.filters.amenities
    assert "Tủ lạnh" in r3.filters.amenities

    # 3. Model nhỏ hay nhầm "tủ lạnh" thành "Bình nước nóng" -> tự gỡ nếu câu không nhắc nước nóng/nl
    r4 = ExtractResult(intent="SEARCH", filters=SearchFilters(amenities=["Bình nước nóng", "WiFi"]))
    apply_explicit_filters(r4, "phòng có wifi và tủ lạnh ở Hà Đông", SAMPLE_CATALOGUE)
    assert "Bình nước nóng" not in r4.filters.amenities
    assert "Tủ lạnh" in r4.filters.amenities

    # 4. Ngụ ý số người ("hai vợ chồng", "ở một mình")
    r5 = ExtractResult(intent="SEARCH", filters=SearchFilters())
    apply_explicit_filters(r5, "hai vợ chồng mình muốn thuê căn hộ ở Đống Đa", SAMPLE_CATALOGUE)
    assert r5.filters.occupants == 2

    r6 = ExtractResult(intent="SEARCH", filters=SearchFilters())
    apply_explicit_filters(r6, "mình đi làm ở một mình nên tìm phòng nhỏ", SAMPLE_CATALOGUE)
    assert r6.filters.occupants == 1


# =============================================================================
# 5. RULES PROVIDER - NGÔN NGỮ ĐỜI THỰC, TIẾNG LÓNG, NGOẠI LỆ (40+ cases)
# =============================================================================


@pytest.mark.parametrize(
    "text,expected",
    [
        # Các định dạng giá đa dạng
        ("phòng dưới 3tr ở Cầu Giấy", {"intent": "SEARCH", "price_max": 3_000_000, "ward": "Cầu Giấy"}),
        ("tim phong tro 2tr5 o ha dong", {"intent": "SEARCH", "price_max": 2_500_000, "ward": "Hà Đông", "room_type": "Phòng trọ"}),
        ("phòng 2m5 ở Cầu Giấy", {"intent": "SEARCH", "price_max": 2_500_000, "ward": "Cầu Giấy"}),
        ("phòng 3 củ quay đầu ở Đống Đa", {"intent": "SEARCH", "price_max": 3_000_000, "ward": "Đống Đa"}),
        ("phòng tầm 4 triệu rưỡi có thang máy", {"intent": "SEARCH", "price_max": 4_500_000, "amenity": "Thang máy"}),
        ("phòng 3500k ở Đống Đa", {"intent": "SEARCH", "price_max": 3_500_000, "ward": "Đống Đa"}),
        ("phòng giá tối đa 2,8 triệu ở Thanh Xuân", {"intent": "SEARCH", "price_max": 2_800_000, "ward": "Thanh Xuân"}),
        ("tìm trọ <3tr có ban công ở Thanh Xuân", {"intent": "SEARCH", "price_max": 3_000_000, "ward": "Thanh Xuân", "amenity": "Ban công"}),
        ("căn hộ giá từ 6 triệu trở lên ở Ba Đình", {"intent": "SEARCH", "price_min": 6_000_000, "ward": "Ba Đình", "room_type": "Căn hộ"}),
        ("phòng 2-3tr ở Cầu Giấy", {"intent": "SEARCH", "price_min": 2_000_000, "price_max": 3_000_000, "ward": "Cầu Giấy"}),
        ("phòng 1tr8 đến 2tr2 cho 2 người ở Long Biên", {"intent": "SEARCH", "price_min": 1_800_000, "price_max": 2_200_000, "occupants": 2, "ward": "Long Biên"}),
        ("căn hộ 4tr5-5tr5 ở Bình Thạnh tphcm", {"intent": "SEARCH", "price_min": 4_500_000, "price_max": 5_500_000, "ward": "Bình Thạnh", "province": "Thành phố Hồ Chí Minh"}),
        ("tìm phòng giá rẻ ở Cầu Giấy", {"intent": "SEARCH", "price_max": 3_000_000, "ward": "Cầu Giấy"}),
        # Các định dạng diện tích (không bị nhầm sang tiền!)
        ("phòng rộng trên 25m2 ở Cầu Giấy", {"intent": "SEARCH", "area_min": 25.0, "price_min": None, "price_max": None, "ward": "Cầu Giấy"}),
        ("phòng nhỏ dưới 20m2 ở Thanh Xuân tối đa 3tr", {"intent": "SEARCH", "area_max": 20.0, "price_max": 3_000_000, "ward": "Thanh Xuân"}),
        ("căn hộ 35-45m2 ở Đống Đa giá 7 triệu", {"intent": "SEARCH", "area_min": 35.0, "area_max": 45.0, "price_max": 7_000_000}),
        ("phòng 22m2 ở Thanh Xuân", {"intent": "SEARCH", "area_min": 22.0, "area_max": 22.0, "ward": "Thanh Xuân"}),
        # Số người ở & quận theo số
        ("tìm phòng trọ quận 7 cho 3 người", {"intent": "SEARCH", "ward": "Quận 7", "occupants": 3}),
        ("ở 1 mình tìm chung cư q.2 có wifi", {"intent": "SEARCH", "ward": "Quận 2", "occupants": 1, "room_type": "Chung cư", "amenity": "WiFi"}),
        ("hai vợ chồng tìm căn hộ ở Đống Đa", {"intent": "SEARCH", "occupants": 2, "room_type": "Căn hộ", "ward": "Đống Đa"}),
        # Câu hỏi nối tiếp rẻ hơn
        ("còn phòng nào rẻ hơn không bạn", {"intent": "FOLLOW_UP"}),
        ("có phòng nào ít tiền hơn không", {"intent": "FOLLOW_UP"}),
        # Ngoài phạm vi
        ("thời tiết Hà Nội hôm nay mưa không", {"intent": "OUT_OF_SCOPE"}),
        ("tỷ lệ bóng đá tối nay thế nào", {"intent": "OUT_OF_SCOPE"}),
        ("chứng khoán hôm nay tăng hay giảm", {"intent": "OUT_OF_SCOPE"}),
        ("lãi suất ngân hàng tháng này bao nhiêu", {"intent": "OUT_OF_SCOPE"}),
        ("tư vấn sức khỏe mùa đông", {"intent": "OUT_OF_SCOPE"}),
        # Quá mơ hồ / không đủ thông tin -> CLARIFY
        ("chỗ nào đẹp đẹp sạch sẽ chill chill", {"intent": "CLARIFY"}),
        ("có phòng không bạn", {"intent": "CLARIFY"}),
        ("???", {"intent": "CLARIFY"}),
        ("asdfghjkl", {"intent": "CLARIFY"}),
        ("😂😂😂", {"intent": "CLARIFY"}),
    ],
)
def test_rules_provider_realistic_extraction(text, expected):
    res = extract_filters_by_rules(text, SAMPLE_CATALOGUE)
    assert res.intent == expected["intent"], f"Input={text!r} -> got {res}"
    if "price_min" in expected:
        assert res.filters.price_min == expected["price_min"]
    if "price_max" in expected:
        assert res.filters.price_max == expected["price_max"]
    if "area_min" in expected:
        assert res.filters.area_min == expected["area_min"]
    if "area_max" in expected:
        assert res.filters.area_max == expected["area_max"]
    if "ward" in expected:
        assert res.filters.ward_or_area == expected["ward"]
    if "province" in expected:
        assert res.filters.province == expected["province"]
    if "room_type" in expected:
        assert res.filters.room_type == expected["room_type"]
    if "occupants" in expected:
        assert res.filters.occupants == expected["occupants"]
    if "amenity" in expected:
        assert expected["amenity"] in res.filters.amenities


def test_rules_provider_ignores_inverted_price_and_area_ranges():
    """Khoảng ngược (5tr-2tr, 40-20m2) không được làm crash RulesProvider."""
    res = extract_filters_by_rules("phòng từ 5 triệu đến 2 triệu diện tích 40-20m2", SAMPLE_CATALOGUE)
    assert res.filters.price_min is None or res.filters.price_max is None or res.filters.price_min <= res.filters.price_max
    assert res.filters.area_min is None or res.filters.area_max is None or res.filters.area_min <= res.filters.area_max


def test_compose_template_handles_empty_normal_and_more_than_5_listings():
    empty_reply = compose_template([])
    assert "chưa tìm thấy" in empty_reply.lower()

    items = [
        {"title": f"Phòng {i}", "price_text": f"{2 + i * 0.2:.1f} triệu/tháng", "area_text": "20 m2", "ward_text": "Cầu Giấy", "amenities": ["WiFi"]}
        for i in range(1, 8)
    ]
    reply = compose_template(items)
    assert "7 phòng" in reply
    assert "5. Phòng 5" in reply
    assert "6. Phòng 6" not in reply
    assert "phòng khác nữa" in reply
