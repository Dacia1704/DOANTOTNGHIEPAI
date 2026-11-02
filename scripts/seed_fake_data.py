"""Sinh dữ liệu giả: tin đăng, tenant theo nhóm, và log tương tác.

CÁCH DÙNG:
    python -m scripts.seed_fake_data --listings 300 --tenants 40

LƯU Ý: dữ liệu giả chỉ chứng minh mô hình nhận ra cấu trúc tự nhúng vào.
Không dùng để kết luận chất lượng với người dùng thật.
"""

from __future__ import annotations

import argparse
import os
import random
import uuid
from datetime import datetime, timedelta

from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import sessionmaker

from app.db.models_core import (
    Amenity,
    CoreRole,
    CoreUser,
    CoreUserRole,
    Listing,
    ListingContactEvent,
    ListingViewHistory,
    Property,
    PropertyType,
    Province,
    Room,
    RoomAmenity,
    RoomType,
    SavedListing,
    TenantProfile,
    TenantProfileAmenity,
    TenantProfileWard,
    Ward,
)

# Nhóm tenant: gu rõ ràng để test gợi ý.
# `amenities` dùng đúng tên trong AMENITY_NAMES (khớp không dấu, không viết tắt).
TENANT_GROUPS = [
    {
        "name": "sinh_vien",
        "tenant_type": "STUDENT",
        "price": (1_500_000, 3_000_000),
        "area": (12, 22),
        "occupants": (1, 2),
        "top_priority": "PRICE",
        "amenities": ["wifi"],
    },
    {
        "name": "nguoi_di_lam",
        "tenant_type": "WORKER",
        "price": (3_000_000, 5_500_000),
        "area": (20, 35),
        "occupants": (1, 2),
        "top_priority": "LOCATION",
        "amenities": ["điều hòa", "bếp"],
    },
    {
        "name": "gia_dinh",
        "tenant_type": "FAMILY",
        "price": (4_000_000, 7_000_000),
        "area": (28, 45),
        "occupants": (2, 4),
        "top_priority": "AREA",
        "amenities": ["điều hòa", "bếp", "nội thất"],
    },
]

ROOM_TYPE_NAMES = [
    ("Phòng trọ", None),
    ("Phòng trọ có điều hoà", "Phòng trọ"),
    ("Phòng trọ mini", None),
    ("Phòng trọ sinh viên", "Phòng trọ"),
]

AMENITY_NAMES = [
    "Điều hòa",
    "WiFi",
    "Nội thất",
    "Bếp",
    "Tủ lạnh",
    "Máy giặt",
    "Ban công",
    "Gác lửng",
    "Để xe máy",
    "Nước nóng",
]

TITLES = [
    "Phòng trọ {ward}, {area}m2 giá {price} triệu",
    "Cho thuê phòng {ward} {area}m2 đầy đủ tiện ích",
    "Phòng {ward} sáng, gần trung tâm, {price} triệu",
    "Căn phòng trọ tại {ward} diện tích {area}m2",
]

DESCRIPTIONS = [
    "Phòng trọ sạch sẽ, thoáng mát, giá thuê hợp lý, đối diện trường và chợ.",
    "Vị trí đẹp, gần xe buýt, nước và internet ổn định, nhiều tiện ích xung quanh.",
    "Phòng mới sửa, nội thất cơ bản, phù hợp cho sinh viên hoặc người đi làm.",
    "Khu yên tĩnh, an ninh tốt, giá rẻ, hợp sinh viên.",
]


def _password_hash() -> str:
    """Hash BCrypt giống cách Spring dùng (BCryptPasswordEncoder), để login được."""
    import bcrypt

    return bcrypt.hashpw(b"Seed@12345", bcrypt.gensalt(rounds=10)).decode("utf-8")


def ensure_base_data(session) -> tuple[dict, list, list]:
    """Đảm bảo có tỉnh, phường, loại phòng, tiện ích và role TENANT."""
    from sqlalchemy import select as sa_select

    province = session.execute(sa_select(Province).limit(1)).scalar_one_or_none()
    if province is None:
        province = Province(id=str(uuid.uuid4()), code="HN", name="Hà Nội")
        session.add(province)
        session.flush()

    wards = (
        session.execute(sa_select(Ward).where(Ward.province_id == province.id))
        .scalars()
        .all()
    )
    if not wards:
        ward_names = [
            "Cầu Giấy",
            "Ba Đình",
            "Hoàn Kiếm",
            "Đống Đa",
            "Hai Bà Trưng",
            "Thanh Xuân",
            "Tây Hồ",
            "Long Biên",
            "Nam Từ Liêm",
            "Hà Đông",
        ]
        for index, name in enumerate(ward_names, start=1):
            session.add(
                Ward(
                    id=str(uuid.uuid4()),
                    province_id=province.id,
                    code=f"WARD{index:03d}",
                    name=name,
                )
            )
        session.flush()
        wards = (
            session.execute(sa_select(Ward).where(Ward.province_id == province.id))
            .scalars()
            .all()
        )

    room_types = session.execute(sa_select(RoomType).limit(1)).scalars().all()
    if not room_types:
        created: dict[str, str] = {}
        for name, parent_name in ROOM_TYPE_NAMES:
            parent_id = created.get(parent_name) if parent_name else None
            room_type = RoomType(
                id=str(uuid.uuid4()),
                name=name,
                description=f"Loại phòng {name}",
                parent_id=parent_id,
            )
            session.add(room_type)
            created[name] = room_type.id
        session.flush()
        room_types = session.execute(sa_select(RoomType)).scalars().all()

    amenities = session.execute(sa_select(Amenity).limit(1)).scalars().all()
    if not amenities:
        for name in AMENITY_NAMES:
            session.add(Amenity(id=str(uuid.uuid4()), name=name))
        session.flush()
        amenities = session.execute(sa_select(Amenity)).scalars().all()

    role = session.execute(
        sa_select(CoreRole).where(CoreRole.code == "TENANT")
    ).scalar_one_or_none()
    if role is None:
        role = CoreRole(id=str(uuid.uuid4()), code="TENANT", name="Tenant")
        session.add(role)
        session.flush()

    return (
        {"province": province, "wards": wards, "room_types": room_types},
        amenities,
        [role],
    )


def seed_listings(session, base, amenities, count: int) -> list[Listing]:
    """Sinh tin đăng đã publish, phòng còn trống.

    Mỗi tin gắn với một nhóm gu (TENANT_GROUPS) để hành vi tenant có
    cấu trúc thực tế, không phải ngẫu nhiên.
    """
    now = datetime.now()
    listings: list[Listing] = []
    listing_groups: dict[str, int] = {}
    amenity_by_name = {amenity.name.lower(): amenity for amenity in amenities}
    for index in range(count):
        group_index = index % len(TENANT_GROUPS)
        group = TENANT_GROUPS[group_index]
        ward = random.choice(base["wards"])
        room_type = random.choice(base["room_types"])
        price_low, price_high = group["price"]
        price = random.randrange(price_low, price_high, 50_000)
        area_low, area_high = group["area"]
        area = random.uniform(area_low, area_high)

        landlord = CoreUser(
            id=str(uuid.uuid4()),
            email=f"landlord{index}@seed.local",
            password=_password_hash(),
            full_name=f"Chủ phòng {index}",
            status="ACTIVE",
        )
        session.add(landlord)
        session.flush()
        property_type = session.scalar(select(PropertyType).limit(1))
        if property_type is None:
            property_type = PropertyType(id=str(uuid.uuid4()), name="Seed property")
            session.add(property_type)
            session.flush()
        property_row = Property(
            id=str(uuid.uuid4()),
            landlord_id=landlord.id,
            property_type_id=property_type.id,
            name=f"Seed property {index}",
            rental_mode="BY_ROOM",
            province_id=base["province"].id,
            ward_id=ward.id,
            address_line=f"{random.randint(1, 200)} đường {ward.name}",
            latitude=round(random.uniform(20.9, 21.2), 8),
            longitude=round(random.uniform(105.6, 105.9), 8),
        )
        session.add(property_row)
        session.flush()
        room = Room(
            id=str(uuid.uuid4()),
            property_id=property_row.id,
            room_number="101",
            room_type_id=room_type.id,
            price=price,
            area_m2=round(area, 2),
            max_occupants=random.choice([1, 2, 3, 4]),
            rental_status="AVAILABLE",
        )
        session.add(room)
        session.flush()

        title = random.choice(TITLES).format(
            ward=ward.name, area=int(area), price=int(price / 1_000_000)
        )
        # Tin được đẩy (priority_level > 0) luôn mới hơn tin thường:
        # chủ nhà trả phí nâng hạng nên cập nhật tin thường xuyên.
        priority_level = random.choice([0, 0, 0, 1, 2])
        if priority_level > 0:
            published_days_ago = random.randint(0, 7)
        else:
            published_days_ago = random.randint(0, 45)

        listing = Listing(
            id=str(uuid.uuid4()),
            property_id=property_row.id,
            title=title,
            description=random.choice(DESCRIPTIONS),
            status="PUBLISHED",
            priority_level=priority_level,
            expires_at=now + timedelta(days=random.randint(10, 60)),
            published_at=now - timedelta(days=published_days_ago),
            created_at=now - timedelta(days=published_days_ago + random.randint(0, 15)),
        )
        session.add(listing)

        # Tiện ích: bắt buộc có tiện ích chính của nhóm, phần còn lại ngẫu nhiên.
        group_amenities = [
            amenity_by_name.get(name.lower()) for name in group["amenities"]
        ]
        group_amenities = [a for a in group_amenities if a is not None]
        extra_count = random.randint(1, 3)
        extras = [
            amenity
            for amenity in random.sample(
                list(amenities),
                k=min(extra_count + len(group_amenities), len(amenities)),
            )
            if amenity not in group_amenities
        ]
        chosen = list(group_amenities) + extras
        for amenity in chosen:
            session.add(
                RoomAmenity(
                    id=str(uuid.uuid4()), room_id=room.id, amenity_id=amenity.id
                )
            )

        listings.append(listing)
        listing_groups[listing.id] = group_index

    session.commit()
    return listings, listing_groups


def seed_tenants(
    session,
    base,
    amenities,
    tenant_role,
    listings: list[Listing],
    listing_groups: dict[str, int],
    count: int,
) -> None:
    """Sinh tenant theo nhóm, kèm profile và hành vi (xem/lưu/liên hệ)."""
    now = datetime.now()
    amenity_by_name = {amenity.name.lower(): amenity for amenity in amenities}

    for index in range(count):
        group = TENANT_GROUPS[index % len(TENANT_GROUPS)]
        user = CoreUser(
            id=str(uuid.uuid4()),
            email=f"tenant{index}@seed.local",
            password=_password_hash(),
            full_name=f"Tenant {index}",
            status="ACTIVE",
        )
        session.add(user)
        session.flush()
        session.add(
            CoreUserRole(id=str(uuid.uuid4()), user_id=user.id, role_id=tenant_role.id)
        )

        price_low, price_high = group["price"]
        area_low, area_high = group["area"]
        occupants_low, occupants_high = group["occupants"]
        ward = random.choice(base["wards"])

        # 20% tenant để trống trường mới để test cold start / profile không đầy đủ.
        sparse = index % 5 == 0

        profile = TenantProfile(
            id=str(uuid.uuid4()),
            user_id=user.id,
            desired_province_id=None if sparse else base["province"].id,
            desired_ward_id=None if sparse else ward.id,
            price_min=None if sparse else price_low,
            price_max=None if sparse else price_high,
            area_min=None if sparse else area_low,
            area_max=None if sparse else area_high,
            desired_room_type_id=None
            if sparse
            else random.choice(base["room_types"]).id,
            tenant_type=group["tenant_type"],
            occupants=random.randint(occupants_low, occupants_high),
            anchor_label=None if sparse else f"Gần trường {ward.name}",
            anchor_latitude=None if sparse else round(random.uniform(20.9, 21.2), 8),
            anchor_longitude=None if sparse else round(random.uniform(105.6, 105.9), 8),
            max_distance_km=None if sparse else round(random.uniform(2, 8), 2),
            price_tolerance_pct=20,
            top_priority=None if sparse else group["top_priority"],
            note=None if sparse else "Ưu tiên phòng có điều hòa",
            # 10% tenant tắt cá nhân hóa.
            allow_personalization=(index % 10 != 0),
        )
        session.add(profile)
        session.flush()

        for amenity_key in group["amenities"]:
            amenity = amenity_by_name.get(amenity_key.lower())
            if amenity is None:
                continue
            session.add(
                TenantProfileAmenity(
                    id=str(uuid.uuid4()),
                    tenant_profile_id=profile.id,
                    amenity_id=amenity.id,
                    is_required=(index % 4 == 0),
                )
            )

        session.add(
            TenantProfileWard(
                id=str(uuid.uuid4()),
                tenant_profile_id=profile.id,
                ward_id=ward.id,
            )
        )

        # Hành vi theo gu nhóm: ưu tiên tin cùng nhóm, trong tầm giá/diện tích.
        group_index = index % len(TENANT_GROUPS)
        preferred = [
            listing
            for listing in listings
            if listing_groups.get(listing.id) == group_index
            and price_low <= listing.room.price <= price_high
            and area_low <= float(listing.room.area_m2) <= area_high
        ]
        if len(preferred) < 5:
            # Bổ sung tin cùng nhóm khi lọc chặt quá ít.
            preferred += [
                listing
                for listing in listings
                if listing_groups.get(listing.id) == group_index
                and listing not in preferred
            ]

        # Tin được lưu / liên hệ là tin người dùng thực sự thích: ưu tiên
        # tin nổi bật và mới đăng (giống hành vi thật).
        ranked = sorted(
            preferred,
            key=lambda item: (
                item.priority_level or 0,
                item.published_at or item.created_at or now,
            ),
            reverse=True,
        )
        liked = ranked[: max(4, len(ranked) // 3)]

        random.shuffle(preferred)
        for listing in preferred[: random.randint(3, 8)]:
            session.add(
                ListingViewHistory(
                    id=str(uuid.uuid4()),
                    tenant_id=user.id,
                    listing_id=listing.id,
                    created_at=now - timedelta(days=random.randint(0, 20)),
                )
            )
        for listing in liked[: random.randint(1, 4)]:
            session.add(
                SavedListing(
                    id=str(uuid.uuid4()),
                    tenant_id=user.id,
                    listing_id=listing.id,
                    created_at=now - timedelta(days=random.randint(0, 20)),
                )
            )
        for listing in liked[: random.randint(0, 2)]:
            session.add(
                ListingContactEvent(
                    id=str(uuid.uuid4()),
                    listing_id=listing.id,
                    tenant_id=user.id,
                    created_at=now - timedelta(days=random.randint(0, 20)),
                )
            )

    session.commit()


def purge_previous_seed(session) -> None:
    """Xoá dữ liệu seed cũ (email @seed.local) để chạy lại không nhân bản."""
    landlord_ids = set(
        session.execute(
            select(CoreUser.id).where(CoreUser.email.like("%@seed.local"))
        ).scalars()
    )
    if not landlord_ids:
        return

    tenant_ids = set(
        session.execute(
            select(CoreUser.id).where(CoreUser.email.like("tenant%@seed.local"))
        ).scalars()
    )
    all_ids = landlord_ids | tenant_ids

    listing_ids = set(
        session.execute(
            select(Listing.id)
            .join(Property, Property.id == Listing.property_id)
            .where(Property.landlord_id.in_(landlord_ids))
        ).scalars()
    )
    if listing_ids:
        room_ids = set(
            session.execute(
                select(Room.id)
                .join(Listing, Listing.property_id == Room.property_id)
                .where(Listing.id.in_(listing_ids))
            ).scalars()
        )
        for model in (
            ListingViewHistory,
            SavedListing,
            ListingContactEvent,
        ):
            session.execute(delete(model).where(model.listing_id.in_(listing_ids)))
        session.execute(delete(RoomAmenity).where(RoomAmenity.room_id.in_(room_ids)))
        session.execute(delete(Listing).where(Listing.id.in_(listing_ids)))
        session.execute(delete(Room).where(Room.id.in_(room_ids)))
        session.execute(delete(Property).where(Property.landlord_id.in_(landlord_ids)))

    if tenant_ids:
        session.execute(
            delete(TenantProfileAmenity).where(
                TenantProfileAmenity.tenant_profile_id.in_(
                    select(TenantProfile.id).where(
                        TenantProfile.user_id.in_(tenant_ids)
                    )
                )
            )
        )
        session.execute(
            delete(TenantProfileWard).where(
                TenantProfileWard.tenant_profile_id.in_(
                    select(TenantProfile.id).where(
                        TenantProfile.user_id.in_(tenant_ids)
                    )
                )
            )
        )
        session.execute(
            delete(TenantProfile).where(TenantProfile.user_id.in_(tenant_ids))
        )

    # Xoá user sau cùng để FK cascade dọn phần còn lại.
    session.execute(delete(CoreUserRole).where(CoreUserRole.user_id.in_(all_ids)))
    session.execute(delete(CoreUser).where(CoreUser.id.in_(all_ids)))
    session.commit()
    print(f"Đã xoá dữ liệu seed cũ: {len(all_ids)} user, {len(listing_ids)} tin đăng.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sinh dữ liệu giả cho AI service")
    parser.add_argument("--listings", type=int, default=200)
    parser.add_argument("--tenants", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--keep-existing",
        action="store_true",
        help="Giữ dữ liệu seed cũ, chỉ thêm dữ liệu mới",
    )
    args = parser.parse_args()

    random.seed(args.seed)

    # Script seed can ghi vao schema chinh, nen dung engine rieng voi tai khoan
    # co quyen ghi (mac dinh la root cua MySQL local).
    engine = create_engine(_seed_db_url(), pool_pre_ping=True, future=True)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    with factory() as session:
        if not args.keep_existing:
            purge_previous_seed(session)
        base, amenities, roles = ensure_base_data(session)
        tenant_role = roles[0]
        print(f"Đã sinh {args.listings} tin đăng...")
        listings, listing_groups = seed_listings(
            session, base, amenities, args.listings
        )
        print(f"Đã sinh {args.tenants} tenant...")
        seed_tenants(
            session,
            base,
            amenities,
            tenant_role,
            listings,
            listing_groups,
            args.tenants,
        )
        print("Xong. Tài khoản seed: tenant0@seed.local / Seed@12345")
    engine.dispose()


def _seed_db_url() -> str:
    value = os.environ.get("SEED_DB_URL")
    if not value:
        raise RuntimeError(
            "Set SEED_DB_URL explicitly for test tools; runtime credentials are never elevated automatically."
        )
    return value


if __name__ == "__main__":
    main()
