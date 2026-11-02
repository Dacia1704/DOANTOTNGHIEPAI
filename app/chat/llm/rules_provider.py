"""Tầng cuối: không dùng LLM, regex + từ điển.

Đây là tầng luôn sống sót. Nếu không hiểu gì thì trả CLARIFY kèm câu gỏi ý.
"""

from __future__ import annotations

import re
from typing import AsyncIterator

from app.chat.normalize import normalize
from app.chat.schemas import ExtractResult, SearchFilters

# Số tiền: "3tr", "3 triệu", "2.5tr", "3,5 triệu", "500k"
_MONEY = re.compile(
    r"(?P<value>\d+(?:[.,]\d+)?)\s*(?P<unit>tr|triệu|trieu|tr\.|k|nghìn|nghin)",
    re.IGNORECASE,
)
_AREA = re.compile(r"(?P<value>\d+(?:[.,]\d+)?)\s*(?:m2|m²|met2|square)", re.IGNORECASE)
# "dưới 3tr", "từ 2 triệu", "2-3tr", "trên 4tr"
_RANGE = re.compile(
    r"(?P<from>\d+(?:[.,]\d+)?)\s*(?P<from_unit>tr|triệu|trieu)?\s*(?:-|–|—|\bden\b|\bđến\b|\btoi\b|\btới\b)\s*"
    r"(?P<to>\d+(?:[.,]\d+)?)\s*(?P<to_unit>tr|triệu|trieu)",
    re.IGNORECASE,
)
_BELOW = re.compile(
    r"(?:dưới|duoi|ko quá|không quá|<|tối đa|toi da)\s*[^0-9]{0,3}(\d+(?:[.,]\d+)?)\s*(tr|triệu|trieu)?",
    re.IGNORECASE,
)
_ABOVE = re.compile(
    r"(?:trên|tren|>=|từ|tu|ít nhất|it nhat)\s*[^0-9]{0,3}(\d+(?:[.,]\d+)?)\s*(tr|triệu|trieu)?",
    re.IGNORECASE,
)

# "rẻ" -> mặc định lấy phòng dưới 3 triệu.
_CHEAP_DEFAULT_MAX = 3_000_000
_CHEAP_WORD = re.compile(r"\bre\b", re.IGNORECASE)

# "quận 7", "quan 7", "q.7" -> khu vực.
_DISTRICT = re.compile(r"\b(?:quan|q\.?|district)\s*(\d{1,2})\b", re.IGNORECASE)

# "cho 2 người", "cho 3 nguoi"
_OCCUPANTS = re.compile(r"\bcho\s*(\d{1,2})\s*(?:nguoi|người|ng)\b", re.IGNORECASE)

# Loại phòng.
_ROOM_TYPE_MAP = {
    "phong tro": "Phòng trọ",
    "nha tro": "Nhà trọ",
    "can ho": "Căn hộ",
    "chung cu": "Chung cư",
}

_OUT_OF_SCOPE_KEYWORDS = (
    "thời tiết", "thoi tiet", "chính trị", "chinh tri", "bóng đá", "bong da",
    "tỷ lệ", "ty le", "chứng khoán", "chung khoan", "tin tức", "tin tuc",
    "dịch covid", "dịch bệnh", "dich benh", "lãi suất", "lai suat",
    "ngân hàng", "ngan hang", "sức khỏe", "suc khoe",
)

_SEARCH_KEYWORDS = (
    "phòng", "phong", "trọ", "tro", "thuê", "thue", "tìm", "tim", "căn", "can",
    "nhà", "nha", "giá rẻ", "gia re", "có", "co", "gần", "gan",
)

_CLARIFY_QUESTION = (
    "Bạn muốn tìm phòng ở khu vực nào và khoảng giá bao nhiêu? "
    "Ví dụ: phòng trọ dưới 3 triệu ở Cầu Giấy."
)


def _to_amount(value: str, unit: str | None) -> int | None:
    number = float(value.replace(",", "."))
    unit = (unit or "").lower()
    if unit.startswith("k") or unit in ("nghìn", "nghin"):
        return int(number * 1000)
    if unit in ("tr", "triệu", "trieu", "tr."):
        return int(number * 1_000_000)
    # Không có đơn vị: coi như triệu nếu số lớn, nếu nhỏ thì đồng.
    if number >= 1000:
        return int(number)
    return int(number * 1_000_000)


def _looks_like_search(text: str) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in _SEARCH_KEYWORDS)


_CHEAPER_FOLLOWUP = ("re hon", "rẻ hơn", "re hơn", "ít tiền hơn", "it tien hon")


def _is_cheaper_followup(lowered: str) -> bool:
    """Cau noi tiep dang hoi "re hon" ma khong co so tien moi."""
    if not any(keyword in lowered for keyword in _CHEAPER_FOLLOWUP):
        return False
    return not _MONEY.search(lowered) and not _RANGE.search(lowered)


def extract_filters_by_rules(text: str, catalogue: dict) -> ExtractResult:
    """Trích bộ lọc bằng regex, đối chiếu tên phường/tiện ích trong danh mục.

    `catalogue` gồm: {"wards": [...], "provinces": [...], "amenities": [...]}
    """
    lowered = text.lower()

    for keyword in _OUT_OF_SCOPE_KEYWORDS:
        if keyword in lowered:
            return ExtractResult(
                intent="OUT_OF_SCOPE",
                filters=SearchFilters(),
                clarification_question=(
                    "Mình chỉ hỗ trợ tìm phòng trọ. Bạn muốn tìm phòng với những tiêu chí gì?"
                ),
            )

    filters = SearchFilters()
    normalized_text = normalize(text)

    # Khoảng giá dạng "2-3tr".
    range_match = _RANGE.search(text)
    if range_match:
        low = _to_amount(range_match.group("from"), range_match.group("from_unit"))
        high = _to_amount(range_match.group("to"), range_match.group("to_unit"))
        if low is not None and high is not None and low <= high:
            filters.price_min = low
            filters.price_max = high

    # "dưới X triệu" / "trên X triệu".
    if filters.price_max is None:
        below = _BELOW.search(text)
        if below:
            amount = _to_amount(below.group(1), below.group(2))
            if amount:
                filters.price_max = amount
    if filters.price_min is None:
        above = _ABOVE.search(text)
        if above:
            amount = _to_amount(above.group(1), above.group(2))
            if amount:
                filters.price_min = amount

    # Số tiền lẻ (chỉ dùng khi chưa có min/max). "giá 3 triệu" = tối đa 3 triệu.
    if filters.price_min is None and filters.price_max is None:
        money = _MONEY.search(text)
        if money:
            amount = _to_amount(money.group("value"), money.group("unit"))
            if amount:
                filters.price_max = amount

    # "rẻ" mà không nêu giá -> mặc định tối đa 3 triệu.
    if filters.price_max is None and _CHEAP_WORD.search(normalized_text):
        filters.price_max = _CHEAP_DEFAULT_MAX

    # Diện tích.
    area = _AREA.search(text)
    if area:
        value = float(area.group("value").replace(",", "."))
        filters.area_min = value
        filters.area_max = value

    # Số người ở.
    occupants = _OCCUPANTS.search(normalized_text)
    if occupants:
        filters.occupants = int(occupants.group(1))

    # Loại phòng.
    for keyword, room_type in _ROOM_TYPE_MAP.items():
        if keyword in normalized_text:
            filters.room_type = room_type
            break

    # Câu nối tiếp dạng "còn phòng nào rẻ hơn không" -> FOLLOW_UP.
    if _is_cheaper_followup(normalized_text):
        return ExtractResult(
            intent="FOLLOW_UP",
            filters=SearchFilters(),
            clarification_question=None,
        )

    # Khu vực: quận/huyện theo số ("quận 7") hoặc tên trong danh mục.
    district = _DISTRICT.search(normalized_text)
    if district:
        filters.ward_or_area = f"Quận {district.group(1)}"
    else:
        for ward in catalogue.get("wards", []):
            if normalize(ward) in normalized_text:
                filters.ward_or_area = ward
                break
        else:
            for province in catalogue.get("provinces", []):
                if normalize(province) in normalized_text:
                    filters.province = province
                    break

    # Tiện ích: cũng so khớp không dấu.
    amenities = []
    for amenity in catalogue.get("amenities", []):
        if normalize(amenity) in normalized_text:
            amenities.append(amenity)
    filters.amenities = amenities

    if filters.is_empty():
        if _looks_like_search(text):
            return ExtractResult(
                intent="CLARIFY",
                filters=SearchFilters(),
                clarification_question=_CLARIFY_QUESTION,
            )
        return ExtractResult(
            intent="CLARIFY",
            filters=SearchFilters(),
            clarification_question=_CLARIFY_QUESTION,
        )

    return ExtractResult(intent="SEARCH", filters=filters)


class RulesProvider:
    """Provider tầng cuối: trích bộ lọc bằng luật, soạn câu trả lời bằng template.

    `catalogue_loader` là hàm đồng bộ trả về {"wards": [...], "provinces": [...],
    "amenities": [...]}, dùng để đối chiếu tên trong câu hỏi với danh mục thật.
    """

    name = "rules"

    def __init__(self, model: str = "rules", catalogue_loader=None) -> None:
        self.model = model
        self.last_usage = None
        self._catalogue_loader = catalogue_loader

    async def extract(self, text: str, last_filters: dict | None) -> ExtractResult:
        catalogue = self._catalogue_loader() if self._catalogue_loader else {}
        return extract_filters_by_rules(text, catalogue)

    async def compose(self, text: str, listings: list[dict]) -> AsyncIterator[str]:
        yield compose_template(listings)


def compose_template(listings: list[dict]) -> str:
    """Tầng cuối: câu trả lời dạng template, không cần LLM."""
    if not listings:
        return (
            "Mình chưa tìm thấy phòng nào phù hợp với yêu cầu. "
            "Bạn thử nới rộng khoảng giá hoặc bỏ bớt tiện ích nhé."
        )
    count = len(listings)
    lines = [f"Mình tìm được {count} phòng phù hợp:"]
    for index, item in enumerate(listings[:5], start=1):
        amenities = ", ".join(item.get("amenities") or [])
        detail = " - ".join(
            part
            for part in (item.get("price_text"), item.get("area_text"), item.get("ward_text"))
            if part
        )
        lines.append(f"{index}. {item.get('title')} ({detail})" + (f" - {amenities}" if amenities else ""))
    if count > 5:
        lines.append("…và một số phòng khác nữa.")
    return "\n".join(lines)
