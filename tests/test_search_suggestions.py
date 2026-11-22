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


class SearchSuggestionsTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        tables = (Province, Ward, RoomType, Property, Listing, Room, Amenity, RoomAmenity, PropertyAmenity)
        CoreBase.metadata.create_all(self.engine, tables=[model.__table__ for model in tables])
        self.session = Session(self.engine)
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.session.close)
        self.session.add(Province(id="hn", code="01", name="Hà Nội"))
        self.session.add_all([
            Ward(id="ha-dong", province_id="hn", code="1", name="Phường Hà Đông", centroid_lat=21, centroid_lng=105),
            Ward(id="nearby", province_id="hn", code="2", name="Phường lân cận", centroid_lat=21.03, centroid_lng=105),
            RoomType(id="room", name="Phòng trọ"),
            Amenity(id="wifi", name="WiFi"),
        ])
        self.session.commit()

    def listing(self, key, price=2_000_000, area=20, ward="ha-dong", km=0, wifi=True, occupants=2):
        now = datetime.now()
        self.session.add(Property(
            id=key, landlord_id="owner", property_type_id="type", name=key,
            rental_mode="BY_ROOM", province_id="hn", ward_id=ward,
            address_line=key, latitude=21 + km / 111.2, longitude=105,
        ))
        self.session.add(Listing(
            id=key, property_id=key, title=key, status="PUBLISHED",
            expires_at=now + timedelta(days=30), published_at=now, created_at=now,
        ))
        self.session.add(Room(
            id=key, property_id=key, room_number=key, room_type_id="room",
            price=price, area_m2=area, max_occupants=occupants, rental_status="AVAILABLE",
        ))
        if wifi:
            self.session.add(RoomAmenity(id=key, room_id=key, amenity_id="wifi"))
        self.session.commit()

    def search(self, **overrides):
        filters = SearchFilters(ward_or_area="Hà Đông", **overrides)
        return search_with_suggestions(self.session, filters)

    def test_exact_match_wins_without_relaxing(self):
        self.listing("exact")
        self.listing("nearby", ward="nearby", km=1)
        items = self.search(price_max=2_000_000)
        self.assertEqual([item["id"] for item in items], ["exact"])
        self.assertNotIn("match_type", items[0])

    def test_price_expands_in_small_steps_and_stops_early(self):
        self.listing("close", price=2_100_000)
        self.listing("further", price=2_300_000)
        items = self.search(price_max=2_000_000)
        self.assertEqual([item["id"] for item in items], ["close"])
        self.assertEqual(items[0]["match_type"], "PRICE")
        self.assertIn("100,000", items[0]["differences"][0])

    def test_second_price_step_works_and_closest_price_ranks_first(self):
        self.listing("closer", price=2_250_000)
        self.listing("newer", price=2_350_000)
        items = self.search(price_max=2_000_000)
        self.assertEqual([item["id"] for item in items], ["closer", "newer"])

    def test_percent_and_absolute_price_limits(self):
        self.listing("too-expensive", price=2_500_000)
        self.assertEqual(self.search(price_max=2_000_000), [])
        self.listing("expensive", price=8_600_000)
        self.assertEqual(self.search(price_min=8_000_000, price_max=8_000_000), [])
        self.assertEqual(expanded_bounds(None, 8_000_000, 20, 500_000)[1], 8_500_000)

    def test_nearby_stages_and_hard_radius_limit(self):
        self.listing("near", ward="nearby", km=2)
        self.listing("farther", ward="nearby", km=4)
        self.listing("too-far", ward="nearby", km=8)
        items = self.search(price_max=2_000_000)
        self.assertEqual([item["id"] for item in items], ["near"])
        self.assertLess(items[0]["distance_km"], 2.5)
        self.assertEqual(items[0]["match_type"], "NEARBY")

    def test_combined_location_price_and_area_expansion(self):
        self.listing("alternative", ward="nearby", km=4, price=2_300_000, area=18)
        items = self.search(price_max=2_000_000, area_min=20)
        self.assertEqual([item["id"] for item in items], ["alternative"])
        self.assertEqual(items[0]["match_type"], "NEARBY_PRICE_AREA")
        self.assertEqual(len(items[0]["differences"]), 3)

    def test_second_radius_step_and_exclusion_beyond_the_limit(self):
        self.listing("within-limit", ward="nearby", km=4)
        self.listing("outside-limit", ward="nearby", km=8)
        items = self.search()
        self.assertEqual([item["id"] for item in items], ["within-limit"])
        self.session.get(Listing, "within-limit").status = "DRAFT"
        self.session.commit()
        self.assertEqual(self.search(), [])

    def test_area_limits_and_explanation(self):
        self.listing("slightly-smaller", area=18)
        items = self.search(area_min=20, area_max=20)
        self.assertEqual(items[0]["match_type"], "AREA")
        self.assertIn("2 m²", items[0]["differences"][0])
        self.listing("ten-square-metres-smaller", area=70)
        self.assertEqual(self.search(area_min=80), [])
        self.assertEqual(expanded_bounds(80, None, 15, 5)[0], 75)

    def test_amenities_capacity_and_room_type_remain_required(self):
        self.listing("missing-wifi", price=2_100_000, wifi=False)
        self.assertEqual(self.search(price_max=2_000_000, amenities=["WiFi"]), [])
        self.assertEqual(self.search(price_max=2_000_000, occupants=3), [])
        self.assertEqual(self.search(price_max=2_000_000, room_type="Căn hộ"), [])

    def test_closest_room_is_selected_inside_a_listing(self):
        self.listing("listing", price=1_850_000)
        self.session.add(Room(
            id="closer-room", property_id="listing", room_number="2", room_type_id="room",
            price=1_980_000, area_m2=20, max_occupants=2, rental_status="AVAILABLE",
        ))
        self.session.commit()
        items = self.search(price_min=2_000_000, price_max=2_000_000)
        self.assertEqual(items[0]["matched_room_id"], "closer-room")

    def test_area_prefixes_resolve_and_ambiguous_provinces_still_require_clarification(self):
        wards, _ = resolve_locations(self.session, "quận Hà Đông")
        self.assertEqual(wards, ["ha-dong"])
        self.session.add(Province(id="other", code="02", name="Tỉnh khác"))
        self.session.add(Ward(id="duplicate", province_id="other", code="3", name="Hà Đông"))
        self.session.commit()
        with self.assertRaises(FilterResolutionError):
            resolve_locations(self.session, "Hà Đông")

    def test_multiple_resolved_wards_can_supply_nearby_centers(self):
        self.session.add(Ward(id="ha-dong-2", province_id="hn", code="3", name="Hà Đông 2", centroid_lat=21.1, centroid_lng=105))
        self.session.commit()
        self.listing("near-second-center", ward="nearby", km=12)
        items = self.search()
        self.assertEqual(items[0]["id"], "near-second-center")
        self.assertLess(items[0]["distance_km"], 2.5)

    def test_no_center_does_not_broaden_to_unrelated_places(self):
        self.session.get(Ward, "ha-dong").centroid_lat = None
        self.session.commit()
        self.listing("unrelated", ward="nearby", km=1)
        self.assertEqual(self.search(), [])

    def test_empty_result_message_respects_the_absolute_budget_cap(self):
        text = compose_suggestions([], SearchFilters(price_max=8_000_000))
        self.assertIn("8.5", text)
        self.assertNotIn("9.6", text)

    def test_original_filters_are_not_mutated(self):
        filters = SearchFilters(ward_or_area="Hà Đông", price_max=2_000_000, area_min=20)
        before = filters.model_dump()
        self.listing("alternative", price=2_300_000, area=18)
        search_with_suggestions(self.session, filters)
        self.assertEqual(filters.model_dump(), before)

    def test_rules_keep_both_ward_and_province_in_a_combined_request(self):
        self.session.add(Province(id="other", code="02", name="Tỉnh khác"))
        self.session.add(Ward(id="duplicate", province_id="other", code="3", name="Hà Đông"))
        self.session.commit()
        self.listing("hanoi-only")
        catalogue = {"wards": ["Phường Hà Đông", "Phường Đông Hà"], "provinces": ["Thành phố Hà Nội", "Tỉnh khác"]}
        for text in ("Tìm phòng ở Hà Đông, Hà Nội", "tim phong o ha dong ha noi", "Tìm trọ ở phường Hà Đông, TP. Hà Nội"):
            with self.subTest(text=text):
                result = extract_filters_by_rules(text, catalogue)
                self.assertEqual(result.filters.province, "Thành phố Hà Nội")
                self.assertEqual(result.filters.ward_or_area, "Hà Đông")
                items = search_with_suggestions(self.session, result.filters)
                self.assertEqual([item["id"] for item in items], ["hanoi-only"])

    def test_multiple_explicit_provinces_are_not_silently_resolved(self):
        names = ["Thành phố Hà Nội", "Tỉnh Hà Nam"]
        self.assertIsNone(named_place("Hà Nội hoặc Hà Nam", names))
        self.assertIsNone(named_place("Hà Nộiiii", names))
