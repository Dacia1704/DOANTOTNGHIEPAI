"""Editable replies for standalone social messages; never swallow search criteria."""

import re

from app.chat.normalize import words

# Add phrases here in normalized Vietnamese (without accents).
SMALL_TALK = (
    (
        r"(?:alo|a lo|hello|helo|hi|hey|chao|xin chao|chao ban|chao em|chao anh|chao chi|ban oi|co ai khong|ban co do khong)(?: (?:ban|nhe|nha|a|oi))*",
        "Chào bạn, mình đây! Mình là trợ lý AI Trọ Tốt. Bạn cần mình hỗ trợ gì về tìm phòng nhé?",
    ),
    (
        r"(?:cam on|cam on ban|cam on nhe|thank you|thanks|thank|da cam on)(?: (?:ban|nhieu|nhe|nha|a))*",
        "Không có gì nhé! Khi cần tìm phòng hoặc điều chỉnh tiêu chí, bạn cứ nhắn mình.",
    ),
    (
        r"(?:tam biet|bye|bai bai|hen gap lai)(?: (?:ban|nhe|nha|a))*",
        "Tạm biệt bạn! Khi cần tìm phòng, bạn cứ quay lại nhắn mình nhé.",
    ),
    (
        r"(?:ban la ai|ban ten gi|ban lam duoc gi|ban giup duoc gi)",
        "Mình là trợ lý AI Trọ Tốt. Mình có thể giúp bạn tìm phòng theo khu vực, ngân sách, diện tích và tiện ích. Bạn đang cần phòng như thế nào?",
    ),
)


def small_talk_reply(text: str) -> str | None:
    normalized = " ".join(words(text))
    for pattern, reply in SMALL_TALK:
        if re.fullmatch(pattern, normalized):
            return reply
    return None
