"""Luồng chatbot: trích bộ lọc -> gộp -> tìm phòng -> soạn câu trả lời -> lưu log."""

from __future__ import annotations

import logging
import re
import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.chat.llm.base import InvalidValueError
from app.chat.normalize import normalize, words
from app.chat.schemas import ExtractResult, SearchFilters
from app.chat.search import (
    ListingCard,
)
from app.db.models_ai import AiConversation, AiMessage
from app.db.models_core import Amenity, Province, TenantProfile, Ward

logger = logging.getLogger("ai.chat")

OUT_OF_SCOPE_REPLY = (
    "Mình chỉ hỗ trợ tìm phòng trọ. "
    "Bạn muốn tìm phòng ở khu vực nào với khoảng giá bao nhiêu nhé?"
)


def load_catalogue(session: Session) -> dict:
    """Tải danh mục phường / tỉnh / tiện ích để đối chiếu tên trong câu hỏi."""
    wards = session.execute(select(Ward.name)).scalars().all()
    provinces = session.execute(select(Province.name)).scalars().all()
    amenities = session.execute(select(Amenity.name)).scalars().all()
    return {
        "wards": list(wards),
        "provinces": list(provinces),
        "amenities": list(amenities),
    }


def ward_name_lookup(session: Session) -> dict[str, str]:
    rows = session.execute(select(Ward.id, Ward.name)).all()
    return {ward_id: name for ward_id, name in rows}


def get_or_create_conversation(
    session: Session, user_id: str, conversation_id: str | None
) -> str:
    if conversation_id:
        conversation = session.get(AiConversation, conversation_id)
        if conversation is not None and conversation.user_id == user_id:
            return conversation.id
        raise HTTPException(status_code=404, detail="Không tìm thấy cuộc hội thoại")
    conversation = AiConversation(id=str(uuid.uuid4()), user_id=user_id)
    session.add(conversation)
    session.commit()
    return conversation.id


def last_filters_of(session: Session, conversation_id: str) -> dict | None:
    """Bộ lọc sau khi gộp của lượt gần nhất, dùng cho câu hỏi nối tiếp."""
    return session.execute(
        select(AiMessage.filters_json)
        .where(
            AiMessage.conversation_id == conversation_id,
            AiMessage.role == "ASSISTANT",
            AiMessage.filters_json.is_not(None),
        )
        .order_by(AiMessage.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def merge_filters(
    delta: SearchFilters,
    previous: dict | None,
) -> SearchFilters:
    """Gộp delta vào bộ lọc trước. Giá trị trong câu hỏi luôn thắng bộ lọc cũ."""
    merged = SearchFilters()
    if previous:
        for key, value in previous.items():
            if value is not None and hasattr(merged, key):
                setattr(merged, key, value)

    delta_data = delta.model_dump(exclude_none=True)

    # Nếu khách đổi sang tỉnh/thành mới mà không nêu phường -> xóa phường thuộc tỉnh cũ.
    if (
        delta.province is not None
        and merged.province is not None
        and delta.province.strip().lower() != merged.province.strip().lower()
        and delta.ward_or_area is None
    ):
        merged.ward_or_area = None

    for key, value in delta_data.items():
        if key == "amenities":
            if value:
                merged.amenities = list(dict.fromkeys([*merged.amenities, *value]))
            continue
        if value is not None:
            setattr(merged, key, value)

    # Nếu lượt mới chỉ cập nhật một biên giá/diện tích và vượt biên cũ -> gỡ biên cũ để tránh mâu thuẫn.
    if merged.price_min is not None and merged.price_max is not None and merged.price_min > merged.price_max:
        if delta.price_max is not None and delta.price_min is None:
            merged.price_min = None
        elif delta.price_min is not None and delta.price_max is None:
            merged.price_max = None

    if merged.area_min is not None and merged.area_max is not None and merged.area_min > merged.area_max:
        if delta.area_max is not None and delta.area_min is None:
            merged.area_min = None
        elif delta.area_min is not None and delta.area_max is None:
            merged.area_max = None

    return SearchFilters.model_validate(merged.model_dump())


def profile_defaults(session: Session, user_id: str) -> dict:
    """Mặc định lấy từ tenant_profiles (chỉ dùng trong code, không vào prompt)."""
    profile = session.execute(
        select(TenantProfile).where(TenantProfile.user_id == user_id)
    ).scalar_one_or_none()
    if profile is None or not profile.allow_personalization:
        return {}

    defaults: dict = {}
    defaults["ward_id"] = profile.desired_ward_id
    if profile.price_min is not None:
        defaults["price_min"] = int(profile.price_min)
    if profile.price_max is not None:
        defaults["price_max"] = int(profile.price_max)
    if profile.area_min is not None:
        defaults["area_min"] = float(profile.area_min)
    if profile.area_max is not None:
        defaults["area_max"] = float(profile.area_max)
    if profile.occupants:
        defaults["occupants"] = profile.occupants
    return defaults


def apply_profile_defaults_to_filters(
    filters: SearchFilters, profile: dict, ward_lookup: dict[str, str]
) -> SearchFilters:
    """Điền mặc định từ profile vào bộ lọc đã trích (giá trị trong câu hỏi thắng)."""
    merged = filters.model_copy(deep=True)

    if (
        merged.ward_or_area is None
        and merged.province is None
        and profile.get("ward_id")
    ):
        name = ward_lookup.get(profile["ward_id"])
        if name:
            merged.ward_or_area = name
    if merged.price_min is None and profile.get("price_min") is not None:
        if merged.price_max is None or profile["price_min"] <= merged.price_max:
            merged.price_min = profile["price_min"]
    if merged.price_max is None and profile.get("price_max") is not None:
        if merged.price_min is None or profile["price_max"] >= merged.price_min:
            merged.price_max = profile["price_max"]
    if merged.area_min is None and profile.get("area_min") is not None:
        if merged.area_max is None or profile["area_min"] <= merged.area_max:
            merged.area_min = profile["area_min"]
    if merged.area_max is None and profile.get("area_max") is not None:
        if merged.area_min is None or profile["area_max"] >= merged.area_min:
            merged.area_max = profile["area_max"]
    if merged.occupants is None and profile.get("occupants"):
        merged.occupants = profile["occupants"]
    return SearchFilters.model_validate(merged.model_dump())


_MIN_PRICE_CUE = re.compile(
    r"(?:>=|(?<![<])>|\b(?:tren|tu|it nhat|tro len|toi thieu|cao hon|dat hon|lon hon)\b)",
    re.IGNORECASE,
)
_NEGATION_PREFIX = re.compile(
    r"(?:\b(?:khong|ko|k|kh|bo|tru)\s+(?:can|lay|muon|dung|phai)?\s*)$",
    re.IGNORECASE,
)
_HOT_WATER_CUES = ("nong lanh", "nuoc nong", "binh nuoc", " nl ")
_COUPLE_CUE = re.compile(r"\b(?:hai|2)\s*vo\s*chong\b|\bvo\s*chong\b", re.IGNORECASE)
_SOLO_CUE = re.compile(r"\b(?:1|mot)\s*minh\b", re.IGNORECASE)


def apply_explicit_filters(
    result: ExtractResult, text: str, catalogue: dict
) -> None:
    """Khôi phục các điều kiện tường minh từ câu hỏi mà model nhỏ dễ bỏ sót hoặc đảo chiều."""
    if result.intent not in ("SEARCH", "FOLLOW_UP"):
        return

    filters = result.filters
    norm_text = normalize(text)
    padded_words = " " + " ".join(words(text)) + " "

    # 1. Giá đơn không kèm từ chỉ hướng tăng ("tầm 2tr5", "2m5", "3 củ") luôn là price_max.
    if (
        filters.price_min is not None
        and filters.price_max is None
        and not _MIN_PRICE_CUE.search(norm_text)
    ):
        filters.price_max = filters.price_min
        filters.price_min = None

    # 2. Khôi phục tiện ích có tên trùng trực tiếp trong catalogue (tôn trọng câu phủ định).
    existing_norm = {normalize(a) for a in filters.amenities}
    for amenity in catalogue.get("amenities", []):
        norm_am = normalize(amenity)
        if not norm_am or norm_am in existing_norm:
            continue
        needle = f" {norm_am} "
        pos = padded_words.find(needle)
        if pos == -1:
            continue
        before = padded_words[max(0, pos - 28):pos + 1]
        if _NEGATION_PREFIX.search(before):
            continue
        filters.amenities.append(amenity)
        existing_norm.add(norm_am)

    # Tránh model nhỏ nhầm "tủ lạnh" thành "Bình nước nóng" khi câu không hề nhắc nước nóng/nl.
    if " tu lanh " in padded_words and not any(cue in padded_words for cue in _HOT_WATER_CUES):
        filters.amenities = [
            a for a in filters.amenities if normalize(a) != "binh nuoc nong"
        ]

    # 3. Ngụ ý số người ở rõ ràng trong tiếng Việt.
    if filters.occupants is None:
        if _COUPLE_CUE.search(norm_text):
            filters.occupants = 2
        elif _SOLO_CUE.search(norm_text):
            filters.occupants = 1


def validate_extracted(
    result: ExtractResult, session: Session, catalogue: dict
) -> None:
    """Kiểm tra giá trị vô lý: khoảng giá hợp lý, tiện ích có thật."""
    filters = result.filters
    if filters.price_min is not None and filters.price_max is not None:
        if filters.price_min > filters.price_max:
            raise InvalidValueError("price_min > price_max")
    if filters.area_min is not None and filters.area_max is not None:
        if filters.area_min > filters.area_max:
            raise InvalidValueError("area_min > area_max")
    if filters.price_max is not None and filters.price_max > 100_000_000:
        raise InvalidValueError("price_max vô lý")
    if filters.area_max is not None and filters.area_max > 1000:
        raise InvalidValueError("area_max vô lý")


def cards_to_payloads(cards: list[ListingCard], session: Session) -> list[dict]:
    """Chuyển card sang payload cho LLM (không có SĐT) và cho frontend."""
    payloads = []
    for card in cards:
        amenities = card.amenities
        price = float(card.price)
        area = float(card.area_m2)
        payloads.append(
            {
                "id": card.id,
                "listing_id": card.id,
                "property_id": card.property_id,
                "matched_room_id": card.matched_room_id,
                "rental_mode": card.rental_mode,
                "title": card.title,
                "price": price,
                "area_m2": area,
                "price_text": f"{price / 1_000_000:.1f} triệu/tháng",
                "area_text": f"{area:.0f} m2",
                "ward_name": card.ward_name,
                "ward_text": card.ward_name or "",
                "room_type_name": card.room_type_name,
                "address_line": card.address_line,
                "amenities": list(amenities),
            }
        )
    return payloads
