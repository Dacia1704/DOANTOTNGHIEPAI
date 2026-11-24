"""Phần 3: Tìm kiếm và gợi ý phòng trọ - Gợi ý cá nhân hóa & Tối ưu đa dạng MMR.

Kiểm thử:
- Lọc cứng (hard_filter_pass): tiện ích bắt buộc, giá vượt ngưỡng tolerance.
- Chấm điểm độ phù hợp profile (profile_score) theo trọng số chuẩn.
- Điểm ưu tiên boost (boost_score).
- Thuật toán đa dạng hóa Maximal Marginal Relevance (MMR):
    + Cân bằng giữa điểm liên quan và độ trùng lặp nội dung.
    + Tránh hiện tượng tất cả phòng gợi ý đều giống hệt nhau hoặc cùng cụm embedding.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
import unittest

import numpy as np

from app.recommend.candidates import Candidate
from app.recommend.rerank import mmr_rerank
from app.recommend.scoring import (
    DEFAULT_WEIGHTS,
    PROFILE_WEIGHTS,
    ProfileSnapshot,
    boost_score,
    freshness_score,
    hard_filter_pass,
    haversine_km,
    occupancy_penalty,
    profile_score,
)


@dataclass
class MockCandidateItem:
    listing_id: str


class RecommendationAndMmrTests(unittest.TestCase):
    """Kiểm thử thuật toán chấm điểm gợi ý và sắp xếp lại MMR."""

    def make_candidate(
        self,
        listing_id: str = "l1",
        price: int = 3_000_000,
        area: int = 25,
        ward_id: str = "w1",
        province_id: str = "p1",
        room_type_id: str = "rt1",
        priority_level: int = 2,
        amenities: tuple[str, ...] = ("wifi",),
    ) -> Candidate:
        now = datetime.now()
        return Candidate(
            listing_id=listing_id,
            title="Phòng đẹp",
            price=Decimal(str(price)),
            area_m2=Decimal(str(area)),
            province_id=province_id,
            ward_id=ward_id,
            room_type_id=room_type_id,
            room_type_name="Phòng trọ",
            ward_name="Phường 1",
            address_line="123 Đường Lớn",
            latitude=Decimal("21.0"),
            longitude=Decimal("105.0"),
            max_occupants=2,
            priority_level=priority_level,
            created_at=now,
            published_at=now,
            amenities=amenities,
            amenity_ids=frozenset(amenities),
        )

    def test_hard_filter_pass_required_amenities(self):
        """Phòng thiếu tiện ích bắt buộc sẽ bị loại hẳn (hard filter)."""
        profile = ProfileSnapshot(required_amenity_ids=frozenset({"wifi", "ac"}))

        candidate_has_both = self.make_candidate(amenities=("wifi", "ac"))
        self.assertTrue(hard_filter_pass(candidate_has_both, profile))

        candidate_missing_ac = self.make_candidate(amenities=("wifi",))
        self.assertFalse(hard_filter_pass(candidate_missing_ac, profile))

    def test_hard_filter_pass_price_tolerance(self):
        """Phòng vượt quá ngưỡng giá dung sai (+20%) bị loại khỏi danh sách ứng viên."""
        profile = ProfileSnapshot(price_max=Decimal("3000000"), price_tolerance_pct=20)

        # 3.5 triệu <= 3 triệu * 1.2 = 3.6 triệu -> Pass
        self.assertTrue(hard_filter_pass(self.make_candidate(price=3_500_000), profile))

        # 3.8 triệu > 3.6 triệu -> Loại
        self.assertFalse(hard_filter_pass(self.make_candidate(price=3_800_000), profile))

    def test_profile_score_matching_room(self):
        """Phòng khớp hoàn toàn các tiêu chí mong muốn đạt điểm profile cao."""
        profile = ProfileSnapshot(
            price_min=Decimal("2000000"),
            price_max=Decimal("4000000"),
            area_min=Decimal("20"),
            area_max=Decimal("30"),
            desired_ward_id="w1",
            desired_province_id="p1",
            desired_room_type_id="rt1",
        )
        cand = self.make_candidate(price=3_000_000, area=25, ward_id="w1", province_id="p1", room_type_id="rt1")
        score = profile_score(cand, profile)

        self.assertGreaterEqual(score, 0.9)

    def test_boost_score_normalization(self):
        """Điểm ưu tiên boost được chuẩn hóa theo priority_level."""
        c_high = self.make_candidate(priority_level=2)
        c_low = self.make_candidate(priority_level=1)
        c_zero = self.make_candidate(priority_level=0)

        self.assertEqual(boost_score(c_high, max_priority=2), 1.0)
        self.assertEqual(boost_score(c_low, max_priority=2), 0.5)
        self.assertEqual(boost_score(c_zero, max_priority=2), 0.0)

    def test_mmr_rerank_picks_highest_relevance_first(self):
        """Lượt chọn đầu tiên của MMR luôn là ứng viên có điểm liên quan cao nhất."""
        c1 = MockCandidateItem("list-1")
        c2 = MockCandidateItem("list-2")
        c3 = MockCandidateItem("list-3")

        candidates = [c1, c2, c3]
        scores = [0.6, 0.95, 0.75]
        vectors = {
            "list-1": np.array([1.0, 0.0]),
            "list-2": np.array([0.0, 1.0]),
            "list-3": np.array([0.5, 0.5]),
        }

        reranked = mmr_rerank(candidates, scores, vectors, top_k=2)

        self.assertEqual(len(reranked), 2)
        self.assertEqual(reranked[0].listing_id, "list-2")

    def test_mmr_promotes_diversity_over_redundancy(self):
        """Khi có 2 ứng viên điểm rất cao nhưng vector giống hệt nhau (trùng lặp), MMR sẽ ưu tiên ứng viên đa dạng hơn."""
        c1 = MockCandidateItem("item-1")
        c2 = MockCandidateItem("item-2-clone")
        c3 = MockCandidateItem("item-3-diverse")

        candidates = [c1, c2, c3]
        scores = [0.90, 0.89, 0.80]
        vectors = {
            "item-1": np.array([1.0, 0.0]),
            "item-2-clone": np.array([1.0, 0.0]),  # similarity với item-1 = 1.0 (trùng lặp hoàn toàn)
            "item-3-diverse": np.array([0.0, 1.0]), # similarity với item-1 = 0.0 (hoàn toàn mới)
        }

        # Với lambda_relevance = 0.5 (cân bằng liên quan và đa dạng)
        reranked = mmr_rerank(candidates, scores, vectors, top_k=2, lambda_relevance=0.5)

        # Kết quả vị trí 0 là item-1
        self.assertEqual(reranked[0].listing_id, "item-1")
        # Vị trí 1 phải là item-3-diverse (thay vì clone item-2) nhờ hình phạt trùng lặp redundancy
        self.assertEqual(reranked[1].listing_id, "item-3-diverse")

    def test_mmr_handles_empty_or_small_inputs(self):
        """MMR xử lý an toàn khi danh sách rỗng hoặc nhỏ hơn top_k."""
        self.assertEqual(mmr_rerank([], [], {}), [])

        single = [MockCandidateItem("list-1")]
        result = mmr_rerank(single, [0.8], {"list-1": np.array([1.0])}, top_k=5)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].listing_id, "list-1")

    def test_haversine_distance_calculation(self):
        """Tính khoảng cách Haversine giữa hai tọa độ địa lý chính xác theo km."""
        # Cùng một tọa độ -> 0 km
        self.assertAlmostEqual(haversine_km(21.0285, 105.8542, 21.0285, 105.8542), 0.0, places=5)
        # Cách khoảng 1 độ vĩ độ (~111.2 km)
        dist = haversine_km(21.0, 105.0, 22.0, 105.0)
        self.assertTrue(110.0 <= dist <= 112.5)

    def test_hard_filter_rejects_excessive_distance_from_anchor(self):
        """Ứng viên nằm xa hơn 1.5 lần max_distance_km so với điểm neo bị loại bởi hard filter."""
        profile = ProfileSnapshot(
            anchor_latitude=Decimal("21.0"),
            anchor_longitude=Decimal("105.0"),
            max_distance_km=Decimal("5"),
        )
        # Khoảng cách 0 km <= 7.5 km -> Pass
        near_cand = self.make_candidate(listing_id="near")
        self.assertTrue(hard_filter_pass(near_cand, profile))

        # Khoảng cách ~11 km > 7.5 km -> Reject
        far_cand = self.make_candidate(listing_id="far")
        far_cand.latitude = Decimal("21.1")
        far_cand.longitude = Decimal("105.0")
        self.assertFalse(hard_filter_pass(far_cand, profile))

    def test_hard_filter_rejects_price_below_lower_tolerance(self):
        """Phòng có giá thấp hơn ngưỡng tối thiểu sau dung sai (-20%) cũng bị lọc cứng."""
        profile = ProfileSnapshot(price_min=Decimal("3000000"), price_tolerance_pct=20)
        # 2.5tr >= 2.4tr -> Pass
        self.assertTrue(hard_filter_pass(self.make_candidate(price=2_500_000), profile))
        # 2.0tr < 2.4tr -> Reject
        self.assertFalse(hard_filter_pass(self.make_candidate(price=2_000_000), profile))

    def test_profile_score_top_priority_boosts_component_weight(self):
        """Khi đặt top_priority='PRICE', phòng giá phù hợp sẽ được nâng trọng số cao hơn phòng khác."""
        base_profile = ProfileSnapshot(
            price_min=Decimal("2000000"),
            price_max=Decimal("3000000"),
            desired_ward_id="w1",
            desired_province_id="p1",
        )
        price_priority_profile = ProfileSnapshot(
            price_min=Decimal("2000000"),
            price_max=Decimal("3000000"),
            desired_ward_id="w1",
            desired_province_id="p1",
            top_priority="PRICE",
        )
        # Phòng đúng giá nhưng sai phường
        good_price_wrong_ward = self.make_candidate(price=2_500_000, ward_id="w_other", province_id="p_other")
        score_normal = profile_score(good_price_wrong_ward, base_profile)
        score_boosted = profile_score(good_price_wrong_ward, price_priority_profile)
        self.assertGreater(score_boosted, score_normal)

    def test_profile_score_preferred_amenities_ratio(self):
        """Điểm tiện ích ưu tiên tăng theo tỷ lệ tiện ích mong muốn mà phòng đáp ứng."""
        profile = ProfileSnapshot(preferred_amenity_ids=frozenset({"wifi", "ac", "fridge", "washer"}))
        cand_all = self.make_candidate(amenities=("wifi", "ac", "fridge", "washer"))
        cand_half = self.make_candidate(amenities=("wifi", "ac"))
        cand_none = self.make_candidate(amenities=())

        self.assertGreater(profile_score(cand_all, profile), profile_score(cand_half, profile))
        self.assertGreater(profile_score(cand_half, profile), profile_score(cand_none, profile))

    def test_freshness_score_exponential_decay(self):
        """Điểm độ mới (freshness) giảm theo hàm mũ exp(-days / 30)."""
        now = datetime.now()
        c_new = self.make_candidate()
        c_new.published_at = now

        c_30d = self.make_candidate()
        c_30d.published_at = now - timedelta(days=30)

        c_60d = self.make_candidate()
        c_60d.published_at = now - timedelta(days=60)

        self.assertAlmostEqual(freshness_score(c_new, now), 1.0, places=3)
        self.assertAlmostEqual(freshness_score(c_30d, now), 0.367879, places=3)
        self.assertLess(freshness_score(c_60d, now), freshness_score(c_30d, now))

    def test_occupancy_penalty_detection(self):
        """Phạt ứng viên có sức chứa nhỏ hơn số người ở trong hồ sơ người thuê."""
        cand = self.make_candidate()
        cand.max_occupants = 2

        self.assertFalse(occupancy_penalty(cand, ProfileSnapshot(occupants=2)))
        self.assertTrue(occupancy_penalty(cand, ProfileSnapshot(occupants=3)))
        self.assertFalse(occupancy_penalty(cand, ProfileSnapshot(occupants=None)))

    def test_mmr_lambda_one_is_pure_relevance(self):
        """Khi lambda_relevance = 1.0, MMR sắp xếp thuần theo điểm liên quan mà bỏ qua phạt trùng lặp."""
        c1 = MockCandidateItem("item-1")
        c2 = MockCandidateItem("item-2-clone")
        c3 = MockCandidateItem("item-3-diverse")
        vectors = {
            "item-1": np.array([1.0, 0.0]),
            "item-2-clone": np.array([1.0, 0.0]),
            "item-3-diverse": np.array([0.0, 1.0]),
        }
        reranked = mmr_rerank([c1, c2, c3], [0.90, 0.89, 0.80], vectors, top_k=2, lambda_relevance=1.0)
        self.assertEqual([c.listing_id for c in reranked], ["item-1", "item-2-clone"])
