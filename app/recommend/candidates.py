"""Candidate rooms belonging to current public property listings."""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from app.db.inventory import (
    amenities_for_rooms,
    inventory_query,
    ordered_inventory,
    require_amenities,
)
from app.db.models_core import Property, Room


@dataclass
class Candidate:
    listing_id: str
    title: str
    price: Decimal
    area_m2: Decimal
    province_id: str
    ward_id: str
    room_type_id: str
    room_type_name: str | None
    ward_name: str | None
    address_line: str | None
    latitude: Decimal | None
    longitude: Decimal | None
    max_occupants: int | None
    priority_level: int
    created_at: object
    published_at: object
    amenities: tuple[str, ...] = ()
    room_id: str = ""
    property_id: str = ""
    rental_mode: str = "BY_ROOM"
    amenity_ids: frozenset[str] = frozenset()


def fetch_candidates(
    session: Session,
    *,
    province_id=None,
    ward_ids=None,
    price_min=None,
    price_max=None,
    tolerance_pct=20,
    required_amenity_ids=(),
    limit=500,
) -> list[Candidate]:
    stmt = inventory_query()
    if province_id:
        stmt = stmt.where(Property.province_id == province_id)
    if ward_ids:
        stmt = stmt.where(Property.ward_id.in_([w for w in ward_ids if w]))
    tolerance = Decimal(tolerance_pct) / 100
    if price_min is not None:
        stmt = stmt.where(Room.price >= price_min * (1 - tolerance))
    if price_max is not None:
        stmt = stmt.where(Room.price <= price_max * (1 + tolerance))
    stmt = require_amenities(stmt, required_amenity_ids)
    rows = session.execute(ordered_inventory(stmt).limit(limit)).all()
    amenities = amenities_for_rooms(session, rows)
    return [
        Candidate(
            listing_id=r.listing_id,
            title=r.title,
            price=r.price,
            area_m2=r.area_m2,
            province_id=r.province_id,
            ward_id=r.ward_id,
            room_type_id=r.room_type_id,
            room_type_name=r.room_type_name,
            ward_name=r.ward_name,
            address_line=r.address_line,
            latitude=r.latitude,
            longitude=r.longitude,
            max_occupants=r.max_occupants,
            priority_level=r.priority_level or 0,
            created_at=r.created_at,
            published_at=r.published_at,
            room_id=r.room_id,
            property_id=r.property_id,
            rental_mode=r.rental_mode,
            amenities=tuple(sorted(amenities[r.room_id].values())),
            amenity_ids=frozenset(amenities[r.room_id]),
        )
        for r in rows
    ]
