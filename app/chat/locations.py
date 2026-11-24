"""Recover explicitly named locations from the catalogue, independently of the LLM."""

import re

from app.chat.normalize import words

# Tên gọi thông dụng / viết tắt tỉnh, thành phố phổ biến ở Việt Nam.
PROVINCE_ALIASES: dict[str, str] = {
    "hcm": "ho chi minh",
    "tp hcm": "ho chi minh",
    "tphcm": "ho chi minh",
    "sai gon": "ho chi minh",
    "sg": "ho chi minh",
    "hn": "ha noi",
    "tp hn": "ha noi",
    "dn": "da nang",
    "hp": "hai phong",
    "ct": "can tho",
}

# Các bí danh đủ rõ nghĩa để trích trực tiếp từ câu chat tự do (tránh viết tắt 2 chữ cái dễ trùng từ).
_FREE_TEXT_PROVINCE_ALIASES: dict[str, str] = {
    "tphcm": "ho chi minh",
    "tp hcm": "ho chi minh",
    "hcm": "ho chi minh",
    "sai gon": "ho chi minh",
}


def place_name(value: str) -> str:
    return re.sub(
        r"^(?:thanh pho|tinh|tp|phuong|quan|huyen|thi xa|thi tran|xa)\s+",
        "",
        " ".join(words(value)),
    )


def canonical_province_needle(value: str) -> str:
    """Chuẩn hóa tên/viết tắt tỉnh thành sang tên chuẩn không dấu."""
    raw = " ".join(words(value))
    if raw in PROVINCE_ALIASES:
        return PROVINCE_ALIASES[raw]
    stripped = place_name(value)
    return PROVINCE_ALIASES.get(stripped, stripped)


def named_place(text: str, names: list[str]) -> str | None:
    segments = [" " + " ".join(words(part)) + " " for part in re.split(r"[,;.!?\n]", text)]
    matches = {
        place_name(name): name for name in names
        if place_name(name) and any(f" {place_name(name)} " in segment for segment in segments)
    }
    if not matches:
        by_key = {place_name(name): name for name in names if place_name(name)}
        for alias, target_key in _FREE_TEXT_PROVINCE_ALIASES.items():
            if target_key in by_key and any(f" {alias} " in segment for segment in segments):
                matches[target_key] = by_key[target_key]
    # Prefer a complete place name over a shorter name contained within it.
    complete = [
        value for key, value in matches.items()
        if not any(key != other and f" {key} " in f" {other} " for other in matches)
    ]
    return complete[0] if len(complete) == 1 else None


def named_ward(text: str, names: list[str], province: str | None) -> str | None:
    if province:
        # Do not join the last ward word and first province word into another
        # ward: "Ha Dong, Ha Noi" must never become "Dong Ha".
        text = " " + " ".join(words(text)) + " "
        text = text.replace(f" {place_name(province)} ", " ")
        for alias, target_key in _FREE_TEXT_PROVINCE_ALIASES.items():
            if target_key == place_name(province):
                text = text.replace(f" {alias} ", " ")
    ward = named_place(text, names)
    # The catalogue can contain both a ward and a commune with the same name
    # in different provinces. Leave the administrative label to DB resolution.
    return re.sub(r"^(?:phường|quận|huyện|thị xã|thị trấn|xã)\s+", "", ward, flags=re.IGNORECASE) if ward else None


def apply_explicit_locations(result, text: str, catalogue: dict) -> None:
    province = named_place(text, catalogue.get("provinces", []))
    ward = named_ward(text, catalogue.get("wards", []), province)
    if province:
        result.filters.province = province
    if ward:
        result.filters.ward_or_area = ward
