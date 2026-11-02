"""Chuỗi fallback: Local → External → Rules.

Quyết định provider trước khi gửi token đầu tiên. Mỗi lần thử ghi một dòng
llm_call_logs (tầng ngoài bị chặn cũng ghi với status tương ứng).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from decimal import Decimal

from app.chat.llm.base import (
    InvalidJsonError,
    InvalidValueError,
    LLMProvider,
    ProviderError,
)
from app.chat.llm.breaker import CircuitBreaker
from app.chat.sanitize import sanitize
from app.chat.schemas import ExtractResult
from app.observability.call_logger import CallLogger, CallRecord
from app.observability.usage import estimate_cost_usd

logger = logging.getLogger("ai.gateway")


class AllProvidersFailed(Exception):
    """Cả ba tầng đều lỗi."""


@dataclass
class CallContext:
    request_id: str
    user_id: str
    step: str


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def classify(exc: Exception) -> str:
    if isinstance(exc, asyncio.TimeoutError):
        return "TIMEOUT"
    if isinstance(exc, InvalidJsonError):
        return "INVALID_JSON"
    if isinstance(exc, InvalidValueError):
        return "INVALID_VALUE"
    if isinstance(exc, ProviderError):
        return "PROVIDER_ERROR"
    return "PROVIDER_ERROR"


def short(exc: Exception, limit: int = 255) -> str:
    """Cắt thông báo lỗi, KHÔNG chứa nội dung prompt."""
    # Provider exceptions may contain request URLs/API keys or raw model content.
    return type(exc).__name__[:limit]


def external_payload(value):
    if isinstance(value, str):
        return sanitize(value)
    if isinstance(value, dict):
        return {key: external_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [external_payload(item) for item in value]
    return value


class Gateway:
    def __init__(
        self,
        chain: list[LLMProvider],
        breakers: dict[str, CircuitBreaker],
        call_logger: CallLogger,
        *,
        timeout: int = 30,
        external_cap_check=None,
    ) -> None:
        self.chain = chain
        self.breakers = breakers
        self.call_logger = call_logger
        self.timeout = timeout
        self._external_cap_check = external_cap_check

    async def extract(
        self,
        ctx: CallContext,
        text: str,
        last_filters: dict | None,
        validator=None,
    ) -> tuple[ExtractResult, str | None]:
        """Trả (kết quả, fallback_reason). `validator` kiểm tra giá trị vô lý."""
        reason: str | None = None
        for attempt_no, provider in enumerate(self.chain, start=1):
            breaker = self.breakers.get(provider.name)
            if breaker and not breaker.allow():
                self._log(ctx, provider, attempt_no, "BREAKER_OPEN")
                reason = f"{provider.name}:breaker_open"
                continue

            if provider.name == "external" and self._external_cap_check is not None:
                if await asyncio.to_thread(self._external_cap_check):
                    self._log(ctx, provider, attempt_no, "CAP_EXCEEDED")
                    reason = "external:cap_exceeded"
                    continue

            payload = sanitize(text) if provider.name == "external" else text
            if hasattr(provider, "last_usage"):
                provider.last_usage = None
            started = time.perf_counter()
            try:
                result = await asyncio.wait_for(
                    provider.extract(
                        payload,
                        external_payload(last_filters)
                        if provider.name == "external"
                        else last_filters,
                    ),
                    self.timeout,
                )
                if validator is not None:
                    validator(result)
                if breaker:
                    breaker.success()
                self._log(
                    ctx,
                    provider,
                    attempt_no,
                    "SUCCESS",
                    is_final=True,
                    latency_ms=_ms(started),
                )
                return result, reason
            except asyncio.CancelledError:
                self._log(
                    ctx, provider, attempt_no, "CANCELLED", latency_ms=_ms(started)
                )
                raise
            except Exception as exc:
                if breaker:
                    breaker.failure()
                status = classify(exc)
                self._log(
                    ctx,
                    provider,
                    attempt_no,
                    status,
                    latency_ms=_ms(started),
                    error_detail=short(exc),
                )
                reason = f"{provider.name}:{status}"
                logger.warning(
                    "provider failed",
                    extra={
                        "step": ctx.step,
                        "provider": provider.name,
                        "model": getattr(provider, "model", None),
                        "status": status,
                        "latency_ms": _ms(started),
                        "fallback_reason": reason,
                    },
                )
        raise AllProvidersFailed(reason or "unknown")

    async def compose_stream(
        self,
        ctx: CallContext,
        text: str,
        listings: list[dict],
    ):
        """Generator trả (provider_name, chuỗi token).

        Provider được chọn TRƯỚC khi gửi token đầu tiên: thử lần lượt qua chuỗi,
        chỉ chuyển tầng khi tầng trước lỗi trước khi phát token nào.
        """
        reason: str | None = None
        for attempt_no, provider in enumerate(self.chain, start=1):
            breaker = self.breakers.get(provider.name)
            if breaker and not breaker.allow():
                self._log(ctx, provider, attempt_no, "BREAKER_OPEN")
                reason = f"{provider.name}:breaker_open"
                continue

            if provider.name == "external" and self._external_cap_check is not None:
                if await asyncio.to_thread(self._external_cap_check):
                    self._log(ctx, provider, attempt_no, "CAP_EXCEEDED")
                    reason = "external:cap_exceeded"
                    continue

            started = time.perf_counter()
            emitted = False
            if hasattr(provider, "last_usage"):
                provider.last_usage = None
            try:
                payload = (
                    external_payload(text) if provider.name == "external" else text
                )
                cards = (
                    external_payload(listings)
                    if provider.name == "external"
                    else listings
                )
                async for chunk in provider.compose(payload, cards):
                    if not emitted:
                        emitted = True
                    yield provider.name, chunk
                if not emitted:
                    # Provider trả rỗng: coi như lỗi để thử tầng sau.
                    raise ProviderError("provider không trả nội dung")
                if breaker:
                    breaker.success()
                self._log(
                    ctx,
                    provider,
                    attempt_no,
                    "SUCCESS",
                    is_final=True,
                    latency_ms=_ms(started),
                )
                return
            except (asyncio.CancelledError, GeneratorExit):
                self._log(
                    ctx, provider, attempt_no, "CANCELLED", latency_ms=_ms(started)
                )
                raise
            except Exception as exc:
                if breaker:
                    breaker.failure()
                status = classify(exc)
                self._log(
                    ctx,
                    provider,
                    attempt_no,
                    status,
                    latency_ms=_ms(started),
                    error_detail=short(exc),
                )
                reason = f"{provider.name}:{status}"
                if emitted:
                    # Đã phát token: không nối tiếp model khác. Báo retry.
                    yield provider.name, None
                    return
                logger.warning(
                    "compose provider failed",
                    extra={
                        "step": ctx.step,
                        "provider": provider.name,
                        "status": status,
                        "fallback_reason": reason,
                    },
                )
        raise AllProvidersFailed(reason or "unknown")

    def _log(
        self,
        ctx: CallContext,
        provider: LLMProvider,
        attempt_no: int,
        status: str,
        *,
        is_final: bool = False,
        latency_ms: int | None = None,
        error_detail: str | None = None,
    ) -> None:
        usage = (
            getattr(provider, "last_usage", None)
            if status not in ("BREAKER_OPEN", "CAP_EXCEEDED")
            else None
        )
        model = getattr(provider, "model", None)
        cost = Decimal("0")
        if provider.name == "external" and usage is not None:
            cost = estimate_cost_usd(model, usage.input_tokens, usage.output_tokens)
        self.call_logger.add(
            CallRecord(
                request_id=ctx.request_id,
                user_id=ctx.user_id,
                step=ctx.step,
                attempt_no=attempt_no,
                provider=provider.name,
                model=model,
                status=status,
                is_final=is_final,
                latency_ms=latency_ms,
                input_tokens=usage.input_tokens if usage else None,
                output_tokens=usage.output_tokens if usage else None,
                est_cost_usd=cost,
                error_detail=error_detail,
            )
        )
