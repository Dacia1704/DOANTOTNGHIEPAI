"""Chấm điểm ứng viên theo profile, hành vi, độ mới và boost.

score = 0.50 * profile + 0.35 * behavior + 0.10 * freshness + 0.05 * boost
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal

from app.recommend.candidates import Candidate

DEFAULT_WEIGHTS = {
    "profile": 0.50,
    "behavior": 0.35,
    "freshness": 0.10,
    "boost": 0.05,
}

PROFILE_WEIGHTS = {
    "price": 0.30,
    "location": 0.25,
    "amenities": 0.20,
    "area": 0.15,
    "room_type": 0.10,
}

PRIORITY_COMPONENT = {
    "PRICE": "price",
    "LOCATION": "location",
    "AREA": "area",
    "AMENITIES": "amenities",
}


@dataclass
class ProfileSnapshot:
    """Các trường profile đã dùng để chấm điểm.

    KHÔNG chứa `note` và `anchor_*` (thông tin nhạy cảm vị trí).
    """

    price_min: Decimal | None = None
    price_max: Decimal | None = None
    area_min: Decimal | None = None
    area_max: Decimal | None = None
    desired_province_id: str | None = None
    desired_ward_id: str | None = None
    extra_ward_ids: tuple[str, ...] = ()
    desired_room_type_id: str | None = None
    tenant_type: str | None = None
    occupants: int | None = None
    anchor_latitude: Decimal | None = None
    anchor_longitude: Decimal | None = None
    max_distance_km: Decimal | None = None
    price_tolerance_pct: int = 20
    top_priority: str | None = None
    allow_personalization: bool = True
    required_amenity_ids: frozenset[str] = frozenset()
    preferred_amenity_ids: frozenset[str] = frozenset()
    amenities_by_candidate: dict[str, frozenset[str]] = field(default_factory=dict)

    def to_json(self) -> dict:
        """Ảnh chụp để lưu vào recommendation_requests.profile_snapshot."""
        return {
            "price_min": str(self.price_min) if self.price_min is not None else None,
            "price_max": str(self.price_max) if self.price_max is not None else None,
            "area_min": str(self.area_min) if self.area_min is not None else None,
            "area_max": str(self.area_max) if self.area_max is not None else None,
            "desired_province_id": self.desired_province_id,
            "desired_ward_id": self.desired_ward_id,
            "extra_ward_ids": list(self.extra_ward_ids),
            "desired_room_type_id": self.desired_room_type_id,
            "tenant_type": self.tenant_type,
            "occupants": self.occupants,
            "max_distance_km": str(self.max_distance_km)
            if self.max_distance_km is not None
            else None,
            "price_tolerance_pct": self.price_tolerance_pct,
            "top_priority": self.top_priority,
            "allow_personalization": self.allow_personalization,
            "required_amenity_count": len(self.required_amenity_ids),
            "preferred_amenity_count": len(self.preferred_amenity_ids),
        }


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    radius = 6371.0
    phi1, phi2 = math.radians(float(lat1)), math.radians(float(lat2))
    d_phi = phi2 - phi1
    d_lambda = math.radians(float(lon2) - float(lon1))
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def _range_score(value, low, high, tolerance_pct: int) -> float:
    """1 nếu trong khoảng, giảm tuyến tính về 0 tại mức vượt tolerance."""
    if value is None:
        return 0.5
    if low is not None and high is not None:
        if low <= value <= high:
            return 1.0
        span = max(float(high - low), 1.0)
        distance = float(low - value) if value < low else float(value - high)
        return max(0.0, 1.0 - distance / (span * (1 + tolerance_pct / 100)))
    if low is not None:
        if value >= low:
            return 1.0
        span = max(float(low) * (tolerance_pct / 100), 1.0)
        return max(0.0, 1.0 - (float(low) - float(value)) / span)
    if high is not None:
        if value <= high:
            return 1.0
        span = max(float(high) * (tolerance_pct / 100), 1.0)
        return max(0.0, 1.0 - (float(value) - float(high)) / span)
    return 0.5


def _price_score(candidate: Candidate, profile: ProfileSnapshot) -> float:
    return _range_score(
        candidate.price, profile.price_min, profile.price_max, profile.price_tolerance_pct
    )


def _area_score(candidate: Candidate, profile: ProfileSnapshot) -> float:
    return _range_score(candidate.area_m2, profile.area_min, profile.area_max, 20)


def _location_score(candidate: Candidate, profile: ProfileSnapshot) -> float:
    if (
        profile.anchor_latitude is not None
        and profile.anchor_longitude is not None
        and candidate.latitude is not None
        and candidate.longitude is not None
    ):
        max_km = float(profile.max_distance_km or 10)
        distance = haversine_km(
            profile.anchor_latitude,
            profile.anchor_longitude,
            candidate.latitude,
            candidate.longitude,
        )
        return max(0.0, min(1.0, 1.0 - distance / max_km))

    if candidate.ward_id == profile.desired_ward_id:
        return 1.0
    if candidate.ward_id in profile.extra_ward_ids:
        return 1.0
    if profile.desired_province_id and candidate.province_id == profile.desired_province_id:
        return 0.5
    return 0.0


def _amenities_score(candidate: Candidate, profile: ProfileSnapshot) -> float:
    if not profile.preferred_amenity_ids:
        return 0.5
    have = candidate.amenity_ids
    hits = len(profile.preferred_amenity_ids & have)
    return hits / len(profile.preferred_amenity_ids)


def _room_type_score(candidate: Candidate, profile: ProfileSnapshot) -> float:
    if not profile.desired_room_type_id:
        return 0.5
    return 1.0 if candidate.room_type_id == profile.desired_room_type_id else 0.0


def profile_score(candidate: Candidate, profile: ProfileSnapshot) -> float:
    """Trung bình có trọng số của các thành phần profile."""
    components = {
        "price": _price_score(candidate, profile),
        "location": _location_score(candidate, profile),
        "amenities": _amenities_score(candidate, profile),
        "area": _area_score(candidate, profile),
        "room_type": _room_type_score(candidate, profile),
    }
    weights = dict(PROFILE_WEIGHTS)

    # top_priority nhân trọng số thành phần tương ứng với 1.5 rồi chuẩn hóa lại.
    priority_component = PRIORITY_COMPONENT.get(profile.top_priority or "")
    if priority_component:
        weights[priority_component] = weights[priority_component] * 1.5
    total = sum(weights.values())
    return sum(components[key] * weights[key] for key in components) / total


def freshness_score(candidate: Candidate, now) -> float:
    """exp(-số ngày kể từ khi đăng / 30)."""
    reference = candidate.published_at or candidate.created_at
    if reference is None:
        return 0.5
    days = max((now - reference).days, 0)
    return math.exp(-days / 30)


def boost_score(candidate: Candidate, max_priority: int = 2) -> float:
    """priority_level chuẩn hóa 0-1. Chỉ là bonus nhỏ."""
    if max_priority <= 0:
        return 0.0
    return min(1.0, max(0.0, candidate.priority_level / max_priority))


def hard_filter_pass(candidate: Candidate, profile: ProfileSnapshot) -> bool:
    """Lọc cứng (loại hẳn): tiện ích bắt buộc, khoảng cách, giá vượt tolerance."""
    have = candidate.amenity_ids
    if profile.required_amenity_ids and not profile.required_amenity_ids.issubset(have):
        return False

    if (
        profile.anchor_latitude is not None
        and profile.anchor_longitude is not None
        and profile.max_distance_km is not None
        and candidate.latitude is not None
        and candidate.longitude is not None
    ):
        distance = haversine_km(
            profile.anchor_latitude,
            profile.anchor_longitude,
            candidate.latitude,
            candidate.longitude,
        )
        if distance > float(profile.max_distance_km) * 1.5:
            return False

    if profile.price_max is not None and candidate.price is not None:
        upper = float(profile.price_max) * (1 + profile.price_tolerance_pct / 100)
        if float(candidate.price) > upper:
            return False
    if profile.price_min is not None and candidate.price is not None:
        lower = float(profile.price_min) * (1 - profile.price_tolerance_pct / 100)
        if float(candidate.price) < lower:
            return False
    return True


def occupancy_penalty(candidate: Candidate, profile: ProfileSnapshot) -> bool:
    """True nghĩa là cần trừ điểm (occupants > max_occupants)."""
    if profile.occupants is None or candidate.max_occupants is None:
        return False
    return profile.occupants > candidate.max_occupants
