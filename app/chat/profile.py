"""Authenticated profile requests are answered from DB without sending private data to an LLM."""

import re
from dataclasses import dataclass, field

from sqlalchemy import select

from app.chat.normalize import normalize
from app.db.models_core import (
    Amenity, Property, Province, Room, RoomType, TenantProfile, TenantProfileAmenity, TenantProfileWard, Ward,
)
from app.recommend.service import recommend
from app.recommend.scoring import haversine_km


def detect_profile_action(text, previous_intent=None):
    text = normalize(text)
    consent = " ".join(re.sub(r"[^a-z0-9 ]", " ", text).split())
    if previous_intent == "PROFILE_SHOW" and re.fullmatch(
            r"(?:co(?: goi y(?: di)?)?|vang|duoc|dong y|ok|oke|goi y(?: di)?|goi y cho (?:toi|minh))(?: nhe)?", consent):
        return "RECOMMEND"
    if not re.search(r"\b(profile|ho so|tieu chi|nhu cau)\b", text):
        return None
    if not re.search(r"\b(cua (toi|minh)|hien tai|theo (profile|ho so|tieu chi|nhu cau)|"
                     r"dua (tren|vao) (profile|ho so))\b", text):
        return "SHOW" if re.fullmatch(r"(?:xem|hien thi) (?:profile|ho so)[.!?]*", text) else None
    if re.search(r"\b(khong (dung|su dung|dua|theo)|bo qua)\b", text):
        return None
    suggest = bool(re.search(r"\b(goi y|de xuat|tim (phong|tro)|phong phu hop)\b", text))
    show = bool(re.search(r"\b(xem|gom|dieu kien|nhu the nao|thong tin|la gi|co nhung)\b", text))
    return "BOTH" if suggest and show else "RECOMMEND" if suggest else "SHOW"


def _number(value):
    return f"{float(value):g}".replace(".", ",")


def _money(value):
    return _number(float(value) / 1_000_000) + " triệu"


def _range(low, high, formatter, unit):
    if low is None and high is None:
        return "Chưa thiết lập"
    if low is None:
        return "Tối đa " + formatter(high) + unit
    if high is None:
        return "Từ " + formatter(low) + unit
    return formatter(low) + " – " + formatter(high) + unit


@dataclass
class ProfileReply:
    text: str
    intent: str = "PROFILE_SHOW"
    status: str = "OK"
    items: list[dict] = field(default_factory=list)
    filters: dict | None = None


def _load(session, user_id):
    # user_id always comes from the verified access token, never from chat text.
    profile = session.scalar(select(TenantProfile).where(TenantProfile.user_id == user_id))
    if profile is None:
        return None
    province = session.get(Province, profile.desired_province_id) if profile.desired_province_id else None
    ward = session.get(Ward, profile.desired_ward_id) if profile.desired_ward_id else None
    if province is None and ward is not None:
        province = session.get(Province, ward.province_id)
    room_type = session.get(RoomType, profile.desired_room_type_id) if profile.desired_room_type_id else None
    extra_wards = list(session.scalars(select(Ward.name)
        .join(TenantProfileWard, TenantProfileWard.ward_id == Ward.id)
        .where(TenantProfileWard.tenant_profile_id == profile.id,
               Ward.id != (profile.desired_ward_id or ""))
        .order_by(Ward.name)))
    amenities = session.execute(select(Amenity.name, TenantProfileAmenity.is_required)
        .join(TenantProfileAmenity, TenantProfileAmenity.amenity_id == Amenity.id)
        .where(TenantProfileAmenity.tenant_profile_id == profile.id).order_by(Amenity.name)).all()
    required = [name for name, required in amenities if required]
    preferred = [name for name, required in amenities if not required]
    filters = {key: value for key, value in {
        "province": province.name if province else None,
        "ward_or_area": ward.name if ward else None,
        "price_min": int(profile.price_min) if profile.price_min is not None else None,
        "price_max": int(profile.price_max) if profile.price_max is not None else None,
        "area_min": float(profile.area_min) if profile.area_min is not None else None,
        "area_max": float(profile.area_max) if profile.area_max is not None else None,
        "room_type": room_type.name if room_type else None,
        "occupants": profile.occupants,
    }.items() if value is not None}
    if required:
        filters["amenities"] = required
    location = ", ".join(name for name in [ward.name if ward else None, province.name if province else None] if name)
    lines = ["Hồ sơ tìm trọ đang lưu của bạn:",
        "- Khu vực: " + (location or "Chưa thiết lập"),
        "- Giá thuê: " + _range(profile.price_min, profile.price_max, _money, "/tháng"),
        "- Diện tích: " + _range(profile.area_min, profile.area_max, _number, " m²"),
        "- Loại phòng: " + (room_type.name if room_type else "Chưa thiết lập"),
        "- Số người ở: " + (str(profile.occupants) if profile.occupants is not None else "Chưa thiết lập"),
        "- Tiện ích bắt buộc: " + (", ".join(required) or "Chưa thiết lập"),
        "- Tiện ích ưu tiên: " + (", ".join(preferred) or "Chưa thiết lập")]
    if extra_wards:
        lines.append("- Phường/xã khác mong muốn: " + ", ".join(extra_wards))
    if profile.tenant_type:
        types = {"STUDENT": "Sinh viên", "WORKER": "Người đi làm", "FAMILY": "Gia đình", "OTHER": "Khác"}
        lines.append("- Nhóm người thuê: " + types.get(profile.tenant_type, profile.tenant_type))
    if profile.move_in_date:
        lines.append("- Ngày dự kiến dọn vào: " + profile.move_in_date.strftime("%d/%m/%Y"))
    if profile.anchor_label:
        lines.append("- Điểm mốc mong muốn: " + profile.anchor_label)
    if profile.max_distance_km is not None:
        lines.append("- Khoảng cách mong muốn tới điểm mốc: " + _number(profile.max_distance_km) + " km")
    if profile.price_tolerance_pct is not None:
        lines.append("- Mức chênh lệch giá chấp nhận: " + str(profile.price_tolerance_pct) + "%")
    if profile.top_priority:
        priorities = {"PRICE": "Giá thuê", "LOCATION": "Vị trí", "AREA": "Diện tích", "AMENITIES": "Tiện ích"}
        lines.append("- Ưu tiên chính: " + priorities.get(profile.top_priority, profile.top_priority))
    if profile.note:
        lines.append("- Ghi chú đã lưu: " + profile.note)
    lines.append("- Cá nhân hóa: " + ("Đang bật" if profile.allow_personalization else "Đang tắt"))
    usable = bool(filters or extra_wards or preferred or (
        profile.anchor_latitude is not None and profile.anchor_longitude is not None))
    return profile, "\n".join(lines), filters, usable


def profile_reply(session, user_id, action, *, request_id):
    loaded = _load(session, user_id)
    intent = "PROFILE_SHOW" if action == "SHOW" else "PROFILE_RECOMMEND"
    if loaded is None:
        return ProfileReply("Bạn chưa lưu hồ sơ tìm trọ. Hãy cập nhật khu vực, khoảng giá và tiện ích "
                            "trong hồ sơ, hoặc cho mình biết các điều kiện bạn muốn tìm.", intent, "CLARIFY")
    profile, summary, filters, usable = loaded
    if action == "SHOW":
        if profile.allow_personalization and usable:
            summary += "\nBạn có muốn mình gợi ý phòng theo hồ sơ này không?"
        return ProfileReply(summary)
    prefix = summary + "\n\n" if action == "BOTH" else ""
    if not profile.allow_personalization:
        return ProfileReply(prefix + "Bạn đang tắt cá nhân hóa nên mình chưa dùng hồ sơ để gợi ý. "
            "Bạn có thể bật lại trong hồ sơ hoặc gửi trực tiếp điều kiện tìm phòng.", intent, "CLARIFY")
    if not usable:
        return ProfileReply(prefix + "Hồ sơ của bạn chưa có tiêu chí tìm phòng. "
            "Bạn muốn tìm ở khu vực nào và khoảng giá bao nhiêu?", intent, "CLARIFY")

    result = recommend(session, user_id, limit=8, source="CHATBOT", request_id=request_id)
    if not result.items:
        return ProfileReply(prefix + "Mình đã dùng hồ sơ hiện tại để tìm nhưng chưa có phòng phù hợp. "
            "Bạn có thể gửi khoảng giá hoặc khu vực mới để mình tìm tiếp.", intent, filters=filters)
    details = {row.id: row for row in session.execute(select(
        Room.id, Room.max_occupants, Property.latitude, Property.longitude)
        .join(Property, Property.id == Room.property_id)
        .where(Room.id.in_([item.matched_room_id for item in result.items])))}
    cards, lines = [], [prefix + "Mình gợi ý các phòng sau dựa trên hồ sơ hiện tại của bạn:"]
    desired_room_type = filters.get("room_type")
    for index, item in enumerate(result.items, 1):
        differences = []
        if profile.price_max is not None and item.price > profile.price_max:
            differences.append("giá cao hơn mức tối đa đã lưu")
        if profile.price_min is not None and item.price < profile.price_min:
            differences.append("giá thấp hơn khoảng đã lưu")
        if profile.area_min is not None and item.area_m2 < profile.area_min:
            differences.append("diện tích nhỏ hơn mong muốn")
        if profile.area_max is not None and item.area_m2 > profile.area_max:
            differences.append("diện tích lớn hơn mong muốn")
        if desired_room_type and item.room_type_name != desired_room_type:
            differences.append("khác loại phòng mong muốn")
        detail = details.get(item.matched_room_id)
        capacity = detail.max_occupants if detail else None
        if profile.occupants and (capacity is None or capacity < profile.occupants):
            differences.append("chưa xác nhận đủ sức chứa" if capacity is None else "sức chứa thấp hơn số người đã lưu")
        if (profile.anchor_latitude is not None and profile.anchor_longitude is not None
                and profile.max_distance_km is not None):
            if detail is None or detail.latitude is None or detail.longitude is None:
                differences.append("chưa có tọa độ để xác nhận khoảng cách tới điểm mốc")
            else:
                distance = haversine_km(profile.anchor_latitude, profile.anchor_longitude,
                                        detail.latitude, detail.longitude)
                if distance > float(profile.max_distance_km):
                    differences.append(f"cách điểm mốc {distance:.1f} km đường thẳng, xa hơn mong muốn")
        card = dict(id=item.listing_id, listing_id=item.listing_id, property_id=item.property_id,
                    matched_room_id=item.matched_room_id, rental_mode=item.rental_mode, title=item.title,
                    price=item.price, area_m2=item.area_m2, price_text=_money(item.price) + "/tháng",
                    area_text=_number(item.area_m2) + " m²", ward_name=item.ward_name, ward_text=item.ward_name,
                    room_type_name=item.room_type_name, address_line=item.address_line, amenities=item.amenities,
                    match_type="PROFILE", differences=differences)
        cards.append(card)
        if index <= 5:
            lines.append(f"{index}. {item.title} — {card['price_text']}, {card['area_text']}, {item.ward_name or ''}."
                         + (" Lưu ý: " + "; ".join(differences) + "." if differences else ""))
    lines.append("Bạn có thể thêm điều kiện cụ thể để mình thu hẹp kết quả.")
    return ProfileReply("\n".join(lines), intent, items=cards, filters=filters)
