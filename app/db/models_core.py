"""Model chỉ đọc cho schema nghiệp vụ (rental_db).

Tách riêng models_ai.py để không ai lỡ ghi vào bảng nghiệp vụ.
"""

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class CoreBase(DeclarativeBase):
    pass


class Province(CoreBase):
    __tablename__ = "provinces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    code: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(100))
    centroid_lat: Mapped[object | None] = mapped_column(Numeric(10, 8))
    centroid_lng: Mapped[object | None] = mapped_column(Numeric(11, 8))


class Ward(CoreBase):
    __tablename__ = "wards"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    province_id: Mapped[str] = mapped_column(String(36), ForeignKey("provinces.id"))
    code: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(100))
    centroid_lat: Mapped[object | None] = mapped_column(Numeric(10, 8))
    centroid_lng: Mapped[object | None] = mapped_column(Numeric(11, 8))


class RoomType(CoreBase):
    __tablename__ = "room_types"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    parent_id: Mapped[str | None] = mapped_column(String(36))


class Amenity(CoreBase):
    __tablename__ = "amenities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    icon: Mapped[str | None] = mapped_column(String(255))


class Listing(CoreBase):
    __tablename__ = "listings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    property_id: Mapped[str] = mapped_column(String(36), ForeignKey("properties.id"))
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20))
    priority_level: Mapped[int] = mapped_column(Integer, default=0)
    view_count: Mapped[int] = mapped_column(Integer, default=0)
    contact_count: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[object | None] = mapped_column(DateTime)
    published_at: Mapped[object | None] = mapped_column(DateTime)
    created_at: Mapped[object] = mapped_column(DateTime)


class PropertyType(CoreBase):
    __tablename__ = "property_types"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))


class Property(CoreBase):
    __tablename__ = "properties"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    landlord_id: Mapped[str] = mapped_column(String(36))
    property_type_id: Mapped[str] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(100))
    rental_mode: Mapped[str] = mapped_column(String(20))
    province_id: Mapped[str] = mapped_column(String(36))
    ward_id: Mapped[str] = mapped_column(String(36))
    address_line: Mapped[str] = mapped_column(String(255))
    latitude: Mapped[object | None] = mapped_column(Numeric(10, 8))
    longitude: Mapped[object | None] = mapped_column(Numeric(11, 8))


class PropertyAmenity(CoreBase):
    __tablename__ = "property_amenities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    property_id: Mapped[str] = mapped_column(String(36))
    amenity_id: Mapped[str] = mapped_column(String(36))


class Room(CoreBase):
    __tablename__ = "rooms"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    property_id: Mapped[str] = mapped_column(String(36), ForeignKey("properties.id"))
    room_number: Mapped[str] = mapped_column(String(30))
    room_type_id: Mapped[str] = mapped_column(String(36), ForeignKey("room_types.id"))
    price: Mapped[object] = mapped_column(Numeric(12, 2))
    area_m2: Mapped[object] = mapped_column(Numeric(8, 2))
    max_occupants: Mapped[int | None] = mapped_column(Integer)
    rental_status: Mapped[str] = mapped_column(String(20))


class RoomAmenity(CoreBase):
    __tablename__ = "room_amenities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    room_id: Mapped[str] = mapped_column(String(36))
    amenity_id: Mapped[str] = mapped_column(String(36))


class TenantProfile(CoreBase):
    __tablename__ = "tenant_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36))
    desired_province_id: Mapped[str | None] = mapped_column(String(36))
    desired_ward_id: Mapped[str | None] = mapped_column(String(36))
    price_min: Mapped[object | None] = mapped_column(Numeric(12, 2))
    price_max: Mapped[object | None] = mapped_column(Numeric(12, 2))
    area_min: Mapped[object | None] = mapped_column(Numeric(8, 2))
    area_max: Mapped[object | None] = mapped_column(Numeric(8, 2))
    desired_room_type_id: Mapped[str | None] = mapped_column(String(36))
    tenant_type: Mapped[str | None] = mapped_column(String(20))
    occupants: Mapped[int | None] = mapped_column(Integer)
    move_in_date: Mapped[object | None] = mapped_column(Date)
    anchor_label: Mapped[str | None] = mapped_column(String(255))
    anchor_latitude: Mapped[object | None] = mapped_column(Numeric(10, 8))
    anchor_longitude: Mapped[object | None] = mapped_column(Numeric(11, 8))
    max_distance_km: Mapped[object | None] = mapped_column(Numeric(5, 2))
    price_tolerance_pct: Mapped[int] = mapped_column(Integer, default=20)
    top_priority: Mapped[str | None] = mapped_column(String(20))
    note: Mapped[str | None] = mapped_column(String(500))
    allow_personalization: Mapped[bool] = mapped_column(Boolean, default=True)


class TenantProfileAmenity(CoreBase):
    __tablename__ = "tenant_profile_amenities"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_profile_id: Mapped[str] = mapped_column(String(36))
    amenity_id: Mapped[str] = mapped_column(String(36))
    is_required: Mapped[bool] = mapped_column(Boolean, default=False)


class TenantProfileWard(CoreBase):
    __tablename__ = "tenant_profile_wards"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_profile_id: Mapped[str] = mapped_column(String(36))
    ward_id: Mapped[str] = mapped_column(String(36))


class ListingViewHistory(CoreBase):
    __tablename__ = "listing_view_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36))
    listing_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[object] = mapped_column(DateTime)


class SavedListing(CoreBase):
    __tablename__ = "saved_listings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36))
    listing_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[object] = mapped_column(DateTime)


class ListingContactEvent(CoreBase):
    __tablename__ = "listing_contact_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    listing_id: Mapped[str] = mapped_column(String(36))
    tenant_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[object] = mapped_column(DateTime)


class CoreUser(CoreBase):
    """Bảng users. AI service chỉ được cấp quyền đọc các cột an toàn.

    Ở đây khai báo đầy đủ để script seed (dùng tài khoản admin) tạo được
    dữ liệu; tài khoản `ai_svc` không có quyền ghi vào bảng này.
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(20))
    password: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    auth_version: Mapped[int] = mapped_column(Integer, default=0)


class CoreUserRole(CoreBase):
    __tablename__ = "user_roles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36))
    role_id: Mapped[str] = mapped_column(String(36))


class CoreRole(CoreBase):
    __tablename__ = "roles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(100))
