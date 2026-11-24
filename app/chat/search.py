"""Parameterized search against the current Property/Room schema."""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.chat.locations import place_name
from app.chat.normalize import normalize
from app.chat.schemas import SearchFilters
from app.db.inventory import (
    amenities_for_rooms,
    inventory_query,
    ordered_inventory,
    require_amenities,
)
from app.db.models_core import Amenity, Property, Province, Room, RoomType, Ward


class FilterResolutionError(ValueError):
    """A requested location/amenity cannot safely be resolved."""


@dataclass(frozen=True)
class ListingCard:
    id: str
    title: str
    price: Decimal
    area_m2: Decimal
    ward_name: str | None
    province_name: str | None
    room_type_name: str | None
    address_line: str | None
    latitude: Decimal | None
    longitude: Decimal | None
    max_occupants: int | None
    amenities: tuple[str, ...]
    priority_level: int
    created_at: object
    published_at: object
    property_id: str = ""
    matched_room_id: str = ""
    rental_mode: str = "BY_ROOM"


def resolve_locations(session: Session, area: str | None, province: str | None = None):
    provinces = session.execute(select(Province.id, Province.name)).all()
    province_ids = []
    if province:
        needle = place_name(province)
        province_ids = [pid for pid, name in provinces if needle and needle in place_name(name)]
        if len(province_ids) != 1:
            raise FilterResolutionError(
                "Bạn vui lòng cho biết rõ tỉnh hoặc thành phố muốn tìm."
            )
    if not area:
        return [], province_ids
    needle = place_name(area)
    wards = session.execute(select(Ward.id, Ward.name, Ward.province_id)).all()
    matched = [
        (wid, pid)
        for wid, name, pid in wards
        if (not province_ids or pid in province_ids)
        and (needle == place_name(name) or needle in place_name(name))
    ]
    if matched:
        if len({pid for _, pid in matched}) > 1:
            raise FilterResolutionError(
                "Khu vực này có ở nhiều tỉnh. Bạn muốn tìm ở tỉnh nào?"
            )
        return [wid for wid, _ in matched], list({pid for _, pid in matched})
    matches = [
        pid
        for pid, name in provinces
        if needle in normalize(name) and (not province_ids or pid in province_ids)
    ]
    if len(matches) == 1:
        return [], matches
    raise FilterResolutionError(
        "Mình chưa xác định được khu vực. Bạn cho biết tên phường và tỉnh nhé?"
    )


def resolve_ward_ids(session: Session, ward_or_area: str | None):
    return resolve_locations(session, ward_or_area)


def resolve_amenity_ids(session: Session, names: list[str]):
    catalogue = session.execute(select(Amenity.id, Amenity.name)).all()
    matched, unknown = [], []
    for raw in names:
        key = normalize(raw)
        exact = [aid for aid, name in catalogue if normalize(name) == key]
        hits = exact or [
            aid for aid, name in catalogue if key and key in normalize(name)
        ]
        if len(hits) == 1:
            matched.extend(hits)
        else:
            unknown.append(raw)
    return list(dict.fromkeys(matched)), unknown


def expanded_bounds(low, high, percent, maximum):
    """Bound each numeric expansion by both a percentage and an absolute gap."""
    def delta(value):
        return min(Decimal(str(value)) * Decimal(str(percent)) / 100, Decimal(str(maximum)))

    return (
        max(Decimal(0), Decimal(str(low)) - delta(low)) if low is not None else None,
        Decimal(str(high)) + delta(high) if high is not None else None,
    )


def _deviation(column, low, high):
    conditions = []
    if low is not None:
        conditions.append((column < low, low - column))
    if high is not None:
        conditions.append((column > high, column - high))
    return case(*conditions, else_=0) if conditions else column * 0


def build_listing_query(filters: SearchFilters, *, price_tolerance_pct=0,
                        price_tolerance_max_vnd=500_000, area_tolerance_pct=0,
                        area_tolerance_max_m2=5, limit=20):
    stmt = inventory_query()
    price_low, price_high = expanded_bounds(filters.price_min, filters.price_max,
                                            price_tolerance_pct, price_tolerance_max_vnd)
    area_low, area_high = expanded_bounds(filters.area_min, filters.area_max,
                                          area_tolerance_pct, area_tolerance_max_m2)
    if price_low is not None:
        stmt = stmt.where(Room.price >= price_low)
    if price_high is not None:
        stmt = stmt.where(Room.price <= price_high)
    if area_low is not None:
        stmt = stmt.where(Room.area_m2 >= area_low)
    if area_high is not None:
        stmt = stmt.where(Room.area_m2 <= area_high)
    if filters.room_type:
        stmt = stmt.where(
            func.lower(RoomType.name) == filters.room_type.strip().lower()
        )
    if filters.occupants is not None:
        stmt = stmt.where(
            or_(Room.max_occupants.is_(None), Room.max_occupants >= filters.occupants)
        )
    return ordered_inventory(stmt).limit(limit)


def fetch_listing_cards(
    session: Session,
    filters: SearchFilters,
    *,
    ward_ids=None,
    province_ids=None,
    amenity_ids=None,
    price_tolerance_pct=0,
    price_tolerance_max_vnd=500_000,
    area_tolerance_pct=0,
    area_tolerance_max_m2=5,
    limit=20,
    property_distances=None,
):
    stmt = build_listing_query(
        filters, price_tolerance_pct=price_tolerance_pct,
        price_tolerance_max_vnd=price_tolerance_max_vnd,
        area_tolerance_pct=area_tolerance_pct,
        area_tolerance_max_m2=area_tolerance_max_m2, limit=None,
    )
    if ward_ids:
        stmt = stmt.where(Property.ward_id.in_(ward_ids))
    if province_ids:
        stmt = stmt.where(Property.province_id.in_(province_ids))
    if property_distances is not None:
        if not property_distances:
            return []
        stmt = stmt.where(Property.id.in_(property_distances))
    stmt = require_amenities(stmt, amenity_ids or ())
    # LIMIT unique listings, not room rows. The window operates after every filter.
    price_gap = _deviation(Room.price, filters.price_min, filters.price_max)
    area_gap = _deviation(Room.area_m2, filters.area_min, filters.area_max)
    relaxed = price_tolerance_pct or area_tolerance_pct
    room_order = (price_gap, area_gap, Room.price, Room.id) if relaxed else (Room.price, Room.id)
    ranked = (
        stmt.order_by(None)
        .add_columns(
            price_gap.label("price_gap"), area_gap.label("area_gap"),
            func.row_number()
            .over(
                partition_by=stmt.selected_columns.listing_id,
                order_by=room_order,
            )
            .label("room_rank")
        )
        .subquery()
    )
    ordering = (ranked.c.published_at.desc(), ranked.c.listing_id)
    if relaxed:
        ordering = (ranked.c.price_gap, ranked.c.area_gap, *ordering)
    if property_distances is not None:
        ordering = (case(property_distances, value=ranked.c.property_id),
                    ranked.c.price_gap, ranked.c.area_gap, ranked.c.price, ranked.c.listing_id)
    rows = session.execute(
        select(ranked)
        .where(ranked.c.room_rank == 1)
        .order_by(*ordering)
        .limit(limit)
    ).all()
    amenities = amenities_for_rooms(session, rows)
    return [
        ListingCard(
            id=r.listing_id,
            title=r.title,
            price=r.price,
            area_m2=r.area_m2,
            ward_name=r.ward_name,
            province_name=r.province_name,
            room_type_name=r.room_type_name,
            address_line=r.address_line,
            latitude=r.latitude,
            longitude=r.longitude,
            max_occupants=r.max_occupants,
            amenities=tuple(sorted(amenities[r.room_id].values())),
            priority_level=r.priority_level or 0,
            created_at=r.created_at,
            published_at=r.published_at,
            property_id=r.property_id,
            matched_room_id=r.room_id,
            rental_mode=r.rental_mode,
        )
        for r in rows
    ]


def load_amenities(session: Session, listing_ids: list[str]):
    # Compatibility helper; payloads now use amenities of their matched Room.
    return {}
