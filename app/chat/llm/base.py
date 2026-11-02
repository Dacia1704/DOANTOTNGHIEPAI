"""Protocol chung cho các tầng LLM."""

from __future__ import annotations

from typing import AsyncIterator, Protocol, runtime_checkable

from app.chat.schemas import ExtractResult


class ProviderError(Exception):
    """Lỗi tầng provider: không kết nối được, timeout, 5xx, model chưa nạp."""


class InvalidJsonError(ProviderError):
    """JSON sai định dạng sau khi đã thử lại 1 lần."""


class InvalidValueError(ProviderError):
    """Đúng JSON nhưng giá trị vô lý (min > max, phường không tồn tại...)."""


class ProviderUsage:
    """Token và chi phí của một lần gọi provider."""

    __slots__ = ("input_tokens", "output_tokens", "est_cost_usd")

    def __init__(
        self,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        est_cost_usd: float = 0.0,
    ) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.est_cost_usd = est_cost_usd


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    model: str

    async def extract(self, text: str, last_filters: dict | None) -> ExtractResult: ...

    def compose(self, text: str, listings: list[dict]) -> AsyncIterator[str]: ...
