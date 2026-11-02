"""Che thông tin cá nhân trước khi gửi câu hỏi ra API nhà cung cấp ngoài."""

from __future__ import annotations

import re

_PHONE = re.compile(r"(?<!\d)(?:\+?84|0)(?:\d[ .-]?){8,10}\d")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_CITIZEN_ID = re.compile(r"(?<!\d)(?:\d{12}|\d{9})(?!\d)")

_PLACEHOLDER = "[ẩn]"


def sanitize(text: str) -> str:
    """Thay số điện thoại, email, CCCD bằng placeholder.

    Chỉ dùng cho provider ngoài (external). Provider local chạy trên máy
    của hệ thống nên không cần che.
    """
    text = _CITIZEN_ID.sub(_PLACEHOLDER, text)
    text = _EMAIL.sub(_PLACEHOLDER, text)
    text = _PHONE.sub(_PLACEHOLDER, text)
    return text
