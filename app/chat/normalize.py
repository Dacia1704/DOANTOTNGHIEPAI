"""Chuẩn hóa chuỗi tiếng Việt để so khớp không dấu.

Người dùng hay gõ không dấu ("cau giay", "dieu hoa") trong khi danh mục
trong DB có dấu ("Cầu Giấy", "Điều hòa"). Chuẩn hóa cả hai về cùng một
dạng không dấu để so khớp chuỗi.
"""

from __future__ import annotations

import re
import unicodedata

# Các cụm từ hay viết tắt / viết khác trong chat (dạng không dấu).
_TYPO_MAP = {
    "phong tro": "phong tro",
    "can ho": "can ho",
    "chung cu": "chung cu",
    "nha tro": "nha tro",
}

_WORD_RE = re.compile(r"[a-z0-9]+")


def strip_accents(text: str) -> str:
    """Bỏ dấu tiếng Việt, giữ chữ thường."""
    if not text:
        return ""
    normalized = unicodedata.normalize("NFD", text)
    without_marks = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    # "đ" không tách dấu theo NFD nên phải quy đổi thủ công.
    without_marks = without_marks.replace("đ", "d").replace("Đ", "D")
    return without_marks.lower().strip()


def normalize(text: str) -> str:
    """Bỏ dấu + gộp khoảng trắng + sửa lỗi viết tắt thường gặp."""
    result = strip_accents(text)
    result = " ".join(result.split())
    for wrong, right in _TYPO_MAP.items():
        if wrong in result:
            result = result.replace(wrong, right)
    return result


def words(text: str) -> list[str]:
    """Tách từ trên chuỗi đã chuẩn hóa."""
    return _WORD_RE.findall(normalize(text))
