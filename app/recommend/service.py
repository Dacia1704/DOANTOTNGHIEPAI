"""Service gợi ý phòng: lọc cứng -> chấm điểm -> xếp hạng + MMR."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models_ai import RecommendationRequest
from app.db.models_core import (
    TenantProfile,
    TenantProfileAmenity,
    TenantProfileWard,
)
from app.embeddings.store import embedding_store
from app.recommend.candidates import Candidate, fetch_candidates
from app.recommend.rerank import mmr_rerank
from app.recommend.scoring import (
    DEFAULT_WEIGHTS,
    ProfileSnapshot,
    boost_score,
    freshness_score,
    hard_filter_pass,
    occupancy_penalty,
    profile_score,
)
from app.recommend.user_vector import build_user_vector, load_interactions

logger = logging.getLogger("ai.recommend")

MODEL_VERSION = "profile-behavior-v1"


@dataclass
class RecommendationItem:
    listing_id: str
    title: str
    price: float
    area_m2: float
    ward_name: str | None
    address_line: str | None
    room_type_name: str | None
    amenities: list[str]
    score: float
    breakdown: dict
    property_id: str = ""
    matched_room_id: str = ""
    rental_mode: str = "BY_ROOM"


@dataclass
class RecommendationResult:
    request_id: str
    items: list[RecommendationItem]
    cold_start: bool
    personalization_used: bool
    candidate_count: int
    latency_ms: int


def load_profile(session: Session, user_id: str) -> ProfileSnapshot | None:
    """Đọc profile của user. Trả None nếu chưa có profile."""
    profile = session.execute(
        select(TenantProfile).where(TenantProfile.user_id == user_id)
    ).scalar_one_or_none()
    if profile is None:
        return None

    amenity_rows = session.execute(
        select(
            TenantProfileAmenity.amenity_id,
            TenantProfileAmenity.is_required,
        ).where(TenantProfileAmenity.tenant_profile_id == profile.id)
    ).all()
    required = frozenset(
        amenity_id for amenity_id, is_required in amenity_rows if is_required
    )
    preferred = frozenset(amenity_id for amenity_id, _ in amenity_rows)

    extra_wards = tuple(
        session.execute(
            select(TenantProfileWard.ward_id).where(
                TenantProfileWard.tenant_profile_id == profile.id
            )
        ).scalars()
    )

    return ProfileSnapshot(
        price_min=profile.price_min,
        price_max=profile.price_max,
        area_min=profile.area_min,
        area_max=profile.area_max,
        desired_province_id=profile.desired_province_id,
        desired_ward_id=profile.desired_ward_id,
        extra_ward_ids=extra_wards,
        desired_room_type_id=profile.desired_room_type_id,
        tenant_type=profile.tenant_type,
        occupants=profile.occupants,
        anchor_latitude=profile.anchor_latitude,
        anchor_longitude=profile.anchor_longitude,
        max_distance_km=profile.max_distance_km,
        price_tolerance_pct=profile.price_tolerance_pct
        if profile.price_tolerance_pct is not None
        else 20,
        top_priority=profile.top_priority,
        allow_personalization=profile.allow_personalization,
        required_amenity_ids=required,
        preferred_amenity_ids=preferred,
    )


def recommend(
    session: Session,
    user_id: str,
    *,
    limit: int = 10,
    source: str = "HOME",
) -> RecommendationResult:
    """Sinh danh sách gợi ý cho user."""
    started = datetime.now()
    request_id = str(uuid.uuid4())

    profile = load_profile(session, user_id) or ProfileSnapshot()
    if not profile.allow_personalization:
        profile = ProfileSnapshot(allow_personalization=False)

    candidates = fetch_candidates(
        session,
        province_id=profile.desired_province_id,
        ward_ids=(
            [profile.desired_ward_id, *profile.extra_ward_ids]
            if profile.desired_ward_id or profile.extra_ward_ids
            else None
        ),
        price_min=profile.price_min,
        price_max=profile.price_max,
        tolerance_pct=profile.price_tolerance_pct,
        required_amenity_ids=profile.required_amenity_ids,
    )
    candidate_count = len(candidates)

    candidates = [c for c in candidates if hard_filter_pass(c, profile)]

    personalization_used = profile.allow_personalization
    user_vector = None
    if personalization_used:
        interactions = load_interactions(session, user_id)
        user_vector, _ = build_user_vector(interactions, embedding_store, started)

    cold_start = user_vector is None
    weights = dict(DEFAULT_WEIGHTS)
    if cold_start:
        # Không có hành vi: chia lại trọng số cho profile, không trả danh sách rỗng.
        profile_share = weights["profile"] + weights["behavior"]
        weights = {
            "profile": profile_share,
            "behavior": 0.0,
            "freshness": weights["freshness"],
            "boost": weights["boost"],
        }

    items = rank_candidates(candidates, profile, user_vector, started, limit, weights)

    latency_ms = int((datetime.now() - started).total_seconds() * 1000)
    _log_request(
        session,
        request_id=request_id,
        user_id=user_id,
        source=source,
        cold_start=cold_start,
        personalization_used=personalization_used,
        profile_snapshot=profile.to_json(),
        weights=weights,
        candidate_count=candidate_count,
        returned_count=len(items),
        listing_ids=[item.listing_id for item in items],
        latency_ms=latency_ms,
    )

    return RecommendationResult(
        request_id=request_id,
        items=items,
        cold_start=cold_start,
        personalization_used=personalization_used,
        candidate_count=candidate_count,
        latency_ms=latency_ms,
    )


def _log_request(
    session: Session,
    *,
    request_id: str,
    user_id: str,
    source: str,
    cold_start: bool,
    personalization_used: bool,
    profile_snapshot: dict,
    weights: dict,
    candidate_count: int,
    returned_count: int,
    listing_ids: list[str],
    latency_ms: int,
) -> None:
    """Ghi log request vào schema `rental_ai` (không ghi vào schema nghiệp vụ)."""
    from app.db.session import ai_session_factory

    with ai_session_factory() as ai_session:
        ai_session.add(
            RecommendationRequest(
                id=request_id,
                user_id=user_id,
                source=source,
                model_version=MODEL_VERSION,
                cold_start=cold_start,
                personalization_used=personalization_used,
                profile_snapshot=profile_snapshot,
                weights=weights,
                candidate_count=candidate_count,
                returned_count=returned_count,
                listing_ids=listing_ids,
                latency_ms=latency_ms,
            )
        )
        ai_session.commit()


def rank_candidates(candidates, profile, user_vector, now, limit, weights):
    scored: list[tuple[Candidate, float, dict]] = []
    for candidate in candidates:
        p_score = profile_score(candidate, profile)
        if occupancy_penalty(candidate, profile):
            p_score *= 0.5

        b_score = 0.0
        if user_vector is not None:
            listing_vector = embedding_store.get(candidate.listing_id)
            if listing_vector is not None:
                b_score = float(np.dot(user_vector, listing_vector))

        f_score = freshness_score(candidate, now)
        boost = boost_score(candidate)

        total = (
            weights["profile"] * p_score
            + weights["behavior"] * b_score
            + weights["freshness"] * f_score
            + weights["boost"] * boost
        )
        breakdown = {
            "profile": round(p_score, 4),
            "behavior": round(b_score, 4),
            "fresh": round(f_score, 4),
            "boost": round(boost, 4),
        }
        scored.append((candidate, total, breakdown))

    scored.sort(key=lambda item: item[1], reverse=True)
    # Keep one real matching room per listing before MMR.
    best_by_listing = {}
    for item in scored:
        best_by_listing.setdefault(item[0].listing_id, item)
    scored = list(best_by_listing.values())

    vectors = {}
    for candidate, _, _ in scored:
        vector = embedding_store.get(candidate.listing_id)
        if vector is not None:
            vectors[candidate.listing_id] = vector

    selected = mmr_rerank(
        [item[0] for item in scored],
        [item[1] for item in scored],
        vectors,
        top_k=limit,
    )
    score_by_id = {
        candidate.listing_id: (score, breakdown)
        for candidate, score, breakdown in scored
    }

    items: list[RecommendationItem] = []
    for candidate in selected:
        score, breakdown = score_by_id[candidate.listing_id]
        items.append(
            RecommendationItem(
                listing_id=candidate.listing_id,
                title=candidate.title,
                price=float(candidate.price),
                area_m2=float(candidate.area_m2),
                ward_name=candidate.ward_name,
                address_line=candidate.address_line,
                room_type_name=candidate.room_type_name,
                amenities=list(candidate.amenities),
                score=round(score, 4),
                breakdown=breakdown,
                property_id=candidate.property_id,
                matched_room_id=candidate.room_id,
                rental_mode=candidate.rental_mode,
            )
        )

    return items
