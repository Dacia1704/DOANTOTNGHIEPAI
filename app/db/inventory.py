"""Read-only Property/Room inventory shared by chat, ranking and embeddings."""

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.db.models_core import (
    Amenity,
    Listing,
    Property,
    PropertyAmenity,
    Province,
    Room,
    RoomAmenity,
    RoomType,
    Ward,
)


def public_conditions():
    # Keep in sync with Spring ListingRepository.PUBLIC_CONDITION.
    newer = aliased(Listing)
    newer_exists = (
        select(newer.id)
        .where(
            newer.property_id == Listing.property_id,
            newer.status == "PUBLISHED",
            newer.expires_at > func.now(),
            or_(
                newer.published_at > Listing.published_at,
                and_(newer.published_at == Listing.published_at, newer.id > Listing.id),
            ),
        )
        .correlate(Listing)
        .exists()
    )
    return (
        Listing.status == "PUBLISHED",
        Listing.expires_at > func.now(),
        ~newer_exists,
        Room.rental_status == "AVAILABLE",
    )


def inventory_query():
    return (
        select(
            Listing.id.label("listing_id"),
            Listing.title,
            Listing.description,
            Listing.priority_level,
            Listing.created_at,
            Listing.published_at,
            Property.id.label("property_id"),
            Property.rental_mode,
            Property.province_id,
            Property.ward_id,
            Property.address_line,
            Property.latitude,
            Property.longitude,
            Room.id.label("room_id"),
            Room.price,
            Room.area_m2,
            Room.max_occupants,
            Room.room_type_id,
            RoomType.name.label("room_type_name"),
            Ward.name.label("ward_name"),
            Province.name.label("province_name"),
        )
        .select_from(Listing)
        .join(Property, Property.id == Listing.property_id)
        .join(Room, Room.property_id == Property.id)
        .join(RoomType, RoomType.id == Room.room_type_id)
        .join(Ward, Ward.id == Property.ward_id)
        .join(Province, Province.id == Property.province_id)
        .where(*public_conditions())
    )


def require_amenities(stmt, amenity_ids):
    # Each required amenity must belong to this Room or its Property, before LIMIT.
    for amenity_id in set(amenity_ids):
        own = (
            select(RoomAmenity.id)
            .where(RoomAmenity.room_id == Room.id, RoomAmenity.amenity_id == amenity_id)
            .correlate(Room)
            .exists()
        )
        shared = (
            select(PropertyAmenity.id)
            .where(
                PropertyAmenity.property_id == Property.id,
                PropertyAmenity.amenity_id == amenity_id,
            )
            .correlate(Property)
            .exists()
        )
        stmt = stmt.where(or_(own, shared))
    return stmt


def ordered_inventory(stmt):
    return stmt.order_by(Listing.published_at.desc(), Listing.id, Room.price, Room.id)


def amenities_for_rooms(session: Session, rows) -> dict[str, dict[str, str]]:
    """Two batched queries; no cross-room union of private amenities."""
    if not rows:
        return {}
    room_ids = {r.room_id for r in rows}
    property_ids = {r.property_id for r in rows}
    own = session.execute(
        select(RoomAmenity.room_id, Amenity.id, Amenity.name)
        .join(Amenity, Amenity.id == RoomAmenity.amenity_id)
        .where(RoomAmenity.room_id.in_(room_ids))
    ).all()
    shared = session.execute(
        select(PropertyAmenity.property_id, Amenity.id, Amenity.name)
        .join(Amenity, Amenity.id == PropertyAmenity.amenity_id)
        .where(PropertyAmenity.property_id.in_(property_ids))
    ).all()
    by_room, by_property = {}, {}
    for key, aid, name in own:
        by_room.setdefault(key, {})[aid] = name
    for key, aid, name in shared:
        by_property.setdefault(key, {})[aid] = name
    return {
        r.room_id: {**by_property.get(r.property_id, {}), **by_room.get(r.room_id, {})}
        for r in rows
    }
