"""Dựng văn bản để embed cho từng tin đăng.

Không đưa giá vào văn bản: số trong văn bản được embed rất kém, giá xử lý
bằng điểm số.
"""

from __future__ import annotations

import hashlib


def build_text(
    title: str,
    description: str | None,
    ward_name: str | None,
    province_name: str | None,
    room_type_name: str | None,
    amenities: list[str],
) -> str:
    parts: list[str] = []
    if title:
        parts.append(title.strip())
    if room_type_name:
        parts.append(f"Loại phòng: {room_type_name}")
    location = " - ".join(p for p in (ward_name, province_name) if p)
    if location:
        parts.append(f"Khu vực: {location}")
    if amenities:
        parts.append("Tiện ích: " + ", ".join(amenities))
    if description:
        cleaned = " ".join(description.split())
        if cleaned:
            parts.append(cleaned[:500])
    return " | ".join(parts)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
