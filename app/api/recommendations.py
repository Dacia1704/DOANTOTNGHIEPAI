"""GET /ai/recommendations - gợi ý phòng cho user hiện tại."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import get_current_tenant
from app.core.security import AuthenticatedUser
from app.db.session import get_core_session
from app.recommend.service import recommend

router = APIRouter(prefix="/ai", tags=["ai"])


@router.get("/recommendations")
def recommendations(
    limit: int = Query(default=10, ge=1, le=50),
    source: str = Query(default="HOME", pattern="^(HOME|SEARCH|CHATBOT)$"),
    user: AuthenticatedUser = Depends(get_current_tenant),
    session: Session = Depends(get_core_session),
) -> dict:
    result = recommend(session, user.id, limit=limit, source=source)
    return {
        "request_id": result.request_id,
        "cold_start": result.cold_start,
        "personalization_used": result.personalization_used,
        "candidate_count": result.candidate_count,
        "latency_ms": result.latency_ms,
        "items": [
            {
                "listing_id": item.listing_id,
                "property_id": item.property_id,
                "matched_room_id": item.matched_room_id,
                "rental_mode": item.rental_mode,
                "title": item.title,
                "price": item.price,
                "area_m2": item.area_m2,
                "ward_name": item.ward_name,
                "address_line": item.address_line,
                "room_type_name": item.room_type_name,
                "amenities": item.amenities,
                "score": item.score,
                "breakdown": item.breakdown,
            }
            for item in result.items
        ],
    }
