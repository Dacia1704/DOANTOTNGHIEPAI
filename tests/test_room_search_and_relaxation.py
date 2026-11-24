"""Phần 3: Tìm kiếm và gợi ý phòng trọ - Trích xuất tiêu chí & Nới lỏng dung sai.

Kiểm thử:
- Tìm kiếm chính xác (Exact match) theo giá, diện tích, khu vực, tiện ích.
- Nới lỏng dung sai khi 0 kết quả (Tolerance Relaxation):
  + Nới lỏng giá từng bước (+10%, +20%, chặn trần tuyệt đối 500.000 đ).
  + Nới lỏng bán kính lân cận (tối đa 5 km từ centroid phường, cùng tỉnh).
  + Nới lỏng diện tích (tối đa 15%, chặn trần 5 m²).
- Bảo toàn tuyệt đối các ràng buộc cứng: loại phòng, tiện ích bắt buộc, sức chứa.
- Chọn phòng tối ưu nhất bên trong tin đăng nhiều phòng.
- Giải thích rõ ràng các tiêu chí được nới lỏng trong thông điệp trả về.
- Bảo đảm tính bất biến (immutability) của bộ lọc gốc.
"""

import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.chat.llm.rules_provider import extract_filters_by_rules
from app.chat.locations import named_place
from app.chat.schemas import SearchFilters
from app.chat.search import FilterResolutionError, expanded_bounds, resolve_locations
from app.chat.suggestions import compose_suggestions, search_with_suggestions
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
    Ward,
)


class RoomSearchAndRelaxationTests(unittest.TestCase):
    """Kiểm thử thuật toán tìm kiếm và nới lỏng dung sai từng bước."""

    def setUp(self):
        self.engine = create_engine("sqlite://")
        tables = (Province, Ward, RoomType, Property, Listing, Room, Amenity, RoomAmenity, PropertyAmenity)
        CoreBase.metadata.create_all(self.engine, tables=[model.__table__ for model in tables])
        self.session = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.session.close)

        self.session.add(Province(id="hn", code="01", name="Hà Nội"))
        self.session.add_all([
            Ward(id="ha-dong", province_id="hn", code="1", name="Phường Hà Đông", centroid_lat=21.0, centroid_lng=105.0),
            Ward(id="nearby", province_id="hn", code="2", name="Phường lân cận", centroid_lat=21.03, centroid_lng=105.0),
            RoomType(id="room", name="Phòng trọ"),
            RoomType(id="apartment", name="Căn hộ"),
            Amenity(id="wifi", name="WiFi"),
            Amenity(id="ac", name="Điều hòa"),
        ])
        self.session.commit()

    def add_listing(self, key, price=2_000_000, area=20, ward="ha-dong", km=0, wifi=True, occupants=2, room_type="room"):
        now = datetime.now()
        self.session.add(Property(
            id=key, landlord_id="owner", property_type_id="type", name=key,
            rental_mode="BY_ROOM", province_id="hn", ward_id=ward,
            address_line=key, latitude=21.0 + km / 111.2, longitude=105.0,
        ))
        self.session.add(Listing(
            id=key, property_id=key, title=key, status="PUBLISHED",
            expires_at=now + timedelta(days=30), published_at=now, created_at=now,
        ))
        self.session.add(Room(
            id=key, property_id=key, room_number=key, room_type_id=room_type,
            price=price, area_m2=area, max_occupants=occupants, rental_status="AVAILABLE",
        ))
        if wifi:
            self.session.add(RoomAmenity(id=key, room_id=key, amenity_id="wifi"))
        self.session.commit()

    def search(self, **overrides):
        filters = SearchFilters(ward_or_area="Hà Đông", **overrides)
        return search_with_suggestions(self.session, filters)

    def test_exact_match_wins_without_relaxation(self):
        """Khi có phòng khớp chính xác yêu cầu, trả về kết quả ngay mà không nới lỏng dung sai."""
        self.add_listing("exact")
        self.add_listing("nearby", ward="nearby", km=1)
        items = self.search(price_max=2_000_000)
        self.assertEqual([item["id"] for item in items], ["exact"])
        self.assertNotIn("match_type", items[0])

    def test_price_expands_in_small_steps_and_stops_early(self):
        """Khi không có phòng đúng giá, nới nhẹ giá (+10%) và dừng ngay khi có phòng phù hợp."""
        self.add_listing("close-price", price=2_100_000)
        self.add_listing("further-price", price=2_300_000)
        items = self.search(price_max=2_000_000)
        self.assertEqual([item["id"] for item in items], ["close-price"])
        self.assertEqual(items[0]["match_type"], "PRICE")
        self.assertIn("100,000", items[0]["differences"][0])

    def test_price_hard_tolerance_limits(self):
        """Nới lỏng giá không vượt quá 20% và tối đa không quá 500.000 đ mỗi biên."""
        self.add_listing("too-expensive", price=2_500_000)
        self.assertEqual(self.search(price_max=2_000_000), [])

        # Với ngân sách 8 triệu, +20% là 9,6 triệu nhưng bị chặn trần 500k -> chỉ tới 8,5 triệu
        bounds = expanded_bounds(None, 8_000_000, 20, 500_000)
        self.assertEqual(bounds[1], 8_500_000)

    def test_nearby_centroid_relaxation_within_5km(self):
        """Mở rộng khu vực lân cận theo centroid tối đa 5 km trong cùng tỉnh."""
        self.add_listing("near", ward="nearby", km=2)
        self.add_listing("farther", ward="nearby", km=4)
        self.add_listing("too-far", ward="nearby", km=8)

        items = self.search(price_max=2_000_000)
        self.assertEqual([item["id"] for item in items], ["near"])
        self.assertLess(items[0]["distance_km"], 2.5)
        self.assertEqual(items[0]["match_type"], "NEARBY")

    def test_area_tolerance_limits(self):
        """Nới lỏng diện tích tối đa 15% nhưng không vượt quá 5 m²."""
        self.add_listing("slightly-smaller", area=18)
        items = self.search(area_min=20, area_max=20)
        self.assertEqual(items[0]["match_type"], "AREA")
        self.assertIn("2 m²", items[0]["differences"][0])

        # Diện tích 80 m², 15% là 12 m² nhưng bị chặn trần 5 m² -> min nới xuống 75 m²
        self.assertEqual(expanded_bounds(80, None, 15, 5)[0], 75)

    def test_hard_invariants_are_strictly_preserved(self):
        """Tiện ích bắt buộc, sức chứa và loại phòng không bị nới lỏng sai lệch."""
        self.add_listing("missing-wifi", price=2_100_000, wifi=False)
        self.assertEqual(self.search(price_max=2_000_000, amenities=["WiFi"]), [])
        self.assertEqual(self.search(price_max=2_000_000, occupants=3), [])
        self.assertEqual(self.search(price_max=2_000_000, room_type="Căn hộ"), [])

    def test_original_filters_remain_immutable(self):
        """Quá trình tìm kiếm mở rộng không làm biến đổi (mutate) đối tượng SearchFilters gốc."""
        filters = SearchFilters(ward_or_area="Hà Đông", price_max=2_000_000, area_min=20)
        original_dict = filters.model_dump()
        self.add_listing("alternative", price=2_300_000, area=18)
        search_with_suggestions(self.session, filters)
        self.assertEqual(filters.model_dump(), original_dict)

    def test_multi_criterion_relaxation_price_and_area(self):
        """Khi không đủ kết quả nới 1 tiêu chí, hệ thống kết hợp nới cả giá và diện tích."""
        self.add_listing("both-relaxed", price=2_200_000, area=18)
        items = self.search(price_max=2_000_000, area_min=20)
        self.assertEqual([item["id"] for item in items], ["both-relaxed"])
        self.assertEqual(items[0]["match_type"], "PRICE_AREA")
        self.assertGreaterEqual(len(items[0]["differences"]), 2)

    def test_nearby_relaxation_respects_province_boundary(self):
        """Mở rộng bán kính lân cận 5 km tuyệt đối không vượt sang tỉnh/thành phố khác."""
        self.session.add(Province(id="hy", code="33", name="Hưng Yên"))
        self.session.add(Ward(id="hy-ward", province_id="hy", code="99", name="Phường Hưng Yên", centroid_lat=21.01, centroid_lng=105.0))
        self.session.commit()

        now = datetime.now()
        self.session.add(Property(
            id="cross-prov", landlord_id="owner", property_type_id="type", name="cross-prov",
            rental_mode="BY_ROOM", province_id="hy", ward_id="hy-ward",
            address_line="Hưng Yên", latitude=21.01, longitude=105.0,
        ))
        self.session.add(Listing(
            id="cross-prov", property_id="cross-prov", title="cross-prov", status="PUBLISHED",
            expires_at=now + timedelta(days=30), published_at=now, created_at=now,
        ))
        self.session.add(Room(
            id="cross-prov", property_id="cross-prov", room_number="101", room_type_id="room",
            price=2_000_000, area_m2=20, max_occupants=2, rental_status="AVAILABLE",
        ))
        self.session.commit()

        # Chỉ cách ~1.1 km nhưng thuộc tỉnh khác -> không được trả về
        self.assertEqual(self.search(price_max=2_000_000), [])

    def test_multi_room_property_picks_closest_matching_room(self):
        """Tin đăng có nhiều phòng sẽ chọn đại diện phòng có giá gần tiêu chí nhất."""
        self.add_listing("multi", price=2_350_000)
        # Thêm phòng thứ 2 giá sát hơn (2.05tr) trong cùng property "multi"
        self.session.add(Room(
            id="multi-r2", property_id="multi", room_number="102", room_type_id="room",
            price=2_050_000, area_m2=20, max_occupants=2, rental_status="AVAILABLE",
        ))
        self.session.add(RoomAmenity(id="multi-r2-wifi", room_id="multi-r2", amenity_id="wifi"))
        self.session.commit()

        items = self.search(price_max=2_000_000)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["price"], 2_050_000)

    def test_rules_provider_extracts_price_area_and_occupants(self):
        """Bộ trích xuất quy tắc (Rules Provider) nhận diện chính xác giá, diện tích, số người từ câu tiếng Việt."""
        catalogue = {
            "provinces": ["Hà Nội"],
            "wards": ["Phường Hà Đông"],
            "amenities": ["WiFi", "Điều hòa"],
        }
        result = extract_filters_by_rules(
            "tìm phòng trọ dưới 3.5 triệu diện tích 25m2 cho 2 người có wifi",
            catalogue,
        )
        self.assertEqual(result.intent, "SEARCH")
        self.assertEqual(result.filters.price_max, 3_500_000)
        self.assertEqual(result.filters.area_min, 25)
        self.assertEqual(result.filters.occupants, 2)

    def test_compose_suggestions_formats_readable_explanation(self):
        """Hàm compose_suggestions tạo câu giải thích rõ ràng lý do nới lỏng cho người dùng."""
        self.add_listing("alt-price", price=2_150_000)
        items = self.search(price_max=2_000_000)
        reply = compose_suggestions(items, SearchFilters(ward_or_area="Hà Đông", price_max=2_000_000))
        self.assertIn("150,000", reply)
