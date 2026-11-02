"""Tầng ngoài: Gemini hoặc Anthropic Claude. Chỉ dùng khi local lỗi."""

from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from app.chat.llm.base import InvalidJsonError, ProviderError, ProviderUsage
from app.chat.prompts import (
    COMPOSE_SYSTEM_PROMPT,
    EXTRACT_SYSTEM_PROMPT,
)
from app.chat.schemas import ExtractResult


class ExternalProvider:
    name = "external"

    def __init__(
        self,
        provider: str,
        model: str,
        gemini_api_key: str = "",
        anthropic_api_key: str = "",
        timeout: int = 30,
    ) -> None:
        self.provider = provider
        self.model = model
        self.gemini_api_key = gemini_api_key
        self.anthropic_api_key = anthropic_api_key
        self.timeout = timeout
        self.last_usage = ProviderUsage()

    @property
    def _is_gemini(self) -> bool:
        return self.provider.lower() == "gemini"

    def _api_key(self) -> str:
        key = self.gemini_api_key if self._is_gemini else self.anthropic_api_key
        if not key:
            raise ProviderError(f"thiếu API key cho provider {self.provider}")
        return key

    async def extract(self, text: str, last_filters: dict | None) -> ExtractResult:
        if self._is_gemini:
            data = await self._gemini_extract(text, last_filters)
        else:
            data = await self._anthropic_extract(text, last_filters)
        raw = data.get("raw")
        if raw is None:
            raise InvalidJsonError("tầng ngoài không trả nội dung JSON")
        try:
            return ExtractResult.model_validate_json(raw)
        except Exception as exc:
            raise InvalidJsonError(f"JSON không hợp lệ: {exc}") from exc

    async def _gemini_extract(self, text: str, last_filters: dict | None) -> dict:
        key = self._api_key()
        contents = [
            {
                "role": "user",
                "parts": [{"text": self._user_content(text, last_filters)}],
            }
        ]
        payload = {
            "systemInstruction": {"parts": [{"text": EXTRACT_SYSTEM_PROMPT}]},
            "contents": contents,
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
                "responseSchema": _gemini_schema(),
            },
        }
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent"
        )
        data = await self._post_json(url, payload, headers={"x-goog-api-key": key})
        usage = data.get("usageMetadata") or {}
        self.last_usage = ProviderUsage(
            input_tokens=usage.get("promptTokenCount"),
            output_tokens=usage.get("candidatesTokenCount"),
            est_cost_usd=0.0,
        )
        candidates = data.get("candidates") or []
        if not candidates:
            raise ProviderError("Gemini không trả candidates")
        parts = (candidates[0].get("content") or {}).get("parts") or []
        raw = "".join(part.get("text", "") for part in parts)
        return {"raw": raw}

    async def _anthropic_extract(self, text: str, last_filters: dict | None) -> dict:
        key = self._api_key()
        payload = {
            "model": self.model,
            "max_tokens": 500,
            "system": EXTRACT_SYSTEM_PROMPT,
            "messages": [
                {"role": "user", "content": self._user_content(text, last_filters)}
            ],
            "tools": [
                {
                    "name": "extract_filters",
                    "description": "Trích bộ lọc tìm phòng từ câu hỏi",
                    "input_schema": _anthropic_schema(),
                }
            ],
            "tool_choice": {"type": "tool", "name": "extract_filters"},
        }
        url = "https://api.anthropic.com/v1/messages"
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        data = await self._post_json(url, payload, headers=headers)
        usage = data.get("usage") or {}
        self.last_usage = ProviderUsage(
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            est_cost_usd=0.0,
        )
        for block in data.get("content") or []:
            if block.get("type") == "tool_use":
                return {"raw": json.dumps(block.get("input"), ensure_ascii=False)}
        raise InvalidJsonError("Anthropic không trả tool_use")

    async def compose(self, text: str, listings: list[dict]) -> AsyncIterator[str]:
        if self._is_gemini:
            async for chunk in self._gemini_compose(text, listings):
                yield chunk
        else:
            async for chunk in self._anthropic_compose(text, listings):
                yield chunk

    async def _gemini_compose(
        self, text: str, listings: list[dict]
    ) -> AsyncIterator[str]:
        key = self._api_key()
        payload = {
            "systemInstruction": {"parts": [{"text": COMPOSE_SYSTEM_PROMPT}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": self._compose_content(text, listings)}],
                }
            ],
            "generationConfig": {"temperature": 0.3},
        }
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:streamGenerateContent?alt=sse"
        )
        async for data in self._stream_sse(
            url, payload, headers={"x-goog-api-key": key}
        ):
            usage = data.get("usageMetadata")
            if usage:
                self.last_usage = ProviderUsage(
                    input_tokens=usage.get("promptTokenCount"),
                    output_tokens=usage.get("candidatesTokenCount"),
                )
            candidates = data.get("candidates") or []
            if not candidates:
                continue
            parts = (candidates[0].get("content") or {}).get("parts") or []
            for part in parts:
                if part.get("text"):
                    yield part["text"]

    async def _anthropic_compose(
        self, text: str, listings: list[dict]
    ) -> AsyncIterator[str]:
        key = self._api_key()
        payload = {
            "model": self.model,
            "max_tokens": 800,
            "stream": True,
            "system": COMPOSE_SYSTEM_PROMPT,
            "messages": [
                {"role": "user", "content": self._compose_content(text, listings)}
            ],
        }
        url = "https://api.anthropic.com/v1/messages"
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        async for data in self._stream_sse(url, payload, headers=headers):
            if data.get("type") == "message_start":
                usage = (data.get("message") or {}).get("usage") or {}
                self.last_usage = ProviderUsage(input_tokens=usage.get("input_tokens"))
            if data.get("type") == "message_delta":
                usage = data.get("usage") or {}
                self.last_usage = ProviderUsage(
                    input_tokens=self.last_usage.input_tokens
                    if self.last_usage
                    else None,
                    output_tokens=usage.get("output_tokens"),
                )
            if data.get("type") == "content_block_delta":
                delta = (data.get("delta") or {}).get("text")
                if delta:
                    yield delta

    # --- helpers ---

    async def _post_json(
        self, url: str, payload: dict, headers: dict | None = None
    ) -> dict:
        request_headers = {"Content-Type": "application/json"}
        if headers:
            request_headers.update(headers)
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, json=payload, headers=request_headers)
        except httpx.TimeoutException as exc:
            raise ProviderError(f"timeout khi gọi API ngoài: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"không gọi được API ngoài: {exc}") from exc

        if response.status_code >= 500:
            raise ProviderError(f"API ngoài trả HTTP {response.status_code}")
        if response.status_code >= 400:
            raise InvalidJsonError(f"API ngoài trả HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise InvalidJsonError("API ngoài trả body không phải JSON") from exc

    async def _stream_sse(
        self, url: str, payload: dict, headers: dict | None = None
    ) -> AsyncIterator[dict]:
        request_headers = {"Content-Type": "application/json"}
        if headers:
            request_headers.update(headers)
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream(
                    "POST", url, json=payload, headers=request_headers
                ) as response:
                    if response.status_code >= 500:
                        raise ProviderError(
                            f"API ngoài trả HTTP {response.status_code}"
                        )
                    if response.status_code >= 400:
                        raise InvalidJsonError(
                            f"API ngoài trả HTTP {response.status_code}"
                        )
                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        chunk = line[5:].strip()
                        if chunk == "[DONE]":
                            break
                        try:
                            yield json.loads(chunk)
                        except ValueError:
                            continue
        except httpx.TimeoutException as exc:
            raise ProviderError(f"timeout khi stream API ngoài: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"không gọi được API ngoài: {exc}") from exc

    @staticmethod
    def _user_content(text: str, last_filters: dict | None) -> str:
        if not last_filters:
            return text
        return (
            "Bộ lọc của lượt trước:\n"
            f"{json.dumps(last_filters, ensure_ascii=False)}\n\n"
            f"Câu hỏi mới: {text}"
        )

    @staticmethod
    def _compose_content(text: str, listings: list[dict]) -> str:
        lines = []
        for index, item in enumerate(listings, start=1):
            amenities = ", ".join(item.get("amenities") or [])
            lines.append(
                f"{index}. {item.get('title')} - {item.get('price_text')} - "
                f"{item.get('area_text')} - {item.get('ward_text')} - {amenities}"
            )
        block = "\n".join(lines) if lines else "(không có phòng nào)"
        return f"Câu hỏi: {text}\n<listing_data>\n{block}\n</listing_data>"


def _gemini_schema() -> dict:
    """Schema tương thích responseSchema của Gemini (OpenAPI subset)."""
    return {
        "type": "OBJECT",
        "properties": {
            "intent": {
                "type": "STRING",
                "enum": ["SEARCH", "FOLLOW_UP", "OUT_OF_SCOPE", "CLARIFY"],
            },
            "filters": {
                "type": "OBJECT",
                "properties": {
                    "province": {"type": "STRING", "nullable": True},
                    "ward_or_area": {"type": "STRING", "nullable": True},
                    "price_min": {"type": "INTEGER", "nullable": True},
                    "price_max": {"type": "INTEGER", "nullable": True},
                    "area_min": {"type": "NUMBER", "nullable": True},
                    "area_max": {"type": "NUMBER", "nullable": True},
                    "room_type": {"type": "STRING", "nullable": True},
                    "amenities": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "occupants": {"type": "INTEGER", "nullable": True},
                },
                "required": [],
            },
            "clarification_question": {"type": "STRING", "nullable": True},
        },
        "required": ["intent", "filters"],
    }


def _anthropic_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "intent": {
                "type": "string",
                "enum": ["SEARCH", "FOLLOW_UP", "OUT_OF_SCOPE", "CLARIFY"],
            },
            "filters": {
                "type": "object",
                "properties": {
                    "province": {"type": ["string", "null"]},
                    "ward_or_area": {"type": ["string", "null"]},
                    "price_min": {"type": ["integer", "null"]},
                    "price_max": {"type": ["integer", "null"]},
                    "area_min": {"type": ["number", "null"]},
                    "area_max": {"type": ["number", "null"]},
                    "room_type": {"type": ["string", "null"]},
                    "amenities": {"type": "array", "items": {"type": "string"}},
                    "occupants": {"type": ["integer", "null"]},
                },
                "required": [],
            },
            "clarification_question": {"type": ["string", "null"]},
        },
        "required": ["intent", "filters"],
    }
