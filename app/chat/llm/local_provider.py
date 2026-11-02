"""Tầng local: Ollama (hoặc vLLM) qua endpoint OpenAI-compatible."""

from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from app.chat.llm.base import InvalidJsonError, ProviderError, ProviderUsage
from app.chat.prompts import (
    COMPOSE_FEWSHOT,
    COMPOSE_SYSTEM_PROMPT,
    EXTRACT_FEWSHOT,
    EXTRACT_SYSTEM_PROMPT,
)
from app.chat.schemas import ExtractResult


class LocalProvider:
    name = "local"

    def __init__(
        self, base_url: str, model: str, api_key: str = "ollama", timeout: int = 30
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.last_usage = ProviderUsage()

    # --- HTTP helpers ---

    async def _post(self, path: str, payload: dict) -> dict:
        url = f"{self.base_url}{path}"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise ProviderError(f"timeout khi gọi local LLM: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"không gọi được local LLM: {exc}") from exc

        if response.status_code >= 500:
            raise ProviderError(f"local LLM trả HTTP {response.status_code}")
        if response.status_code >= 400:
            raise InvalidJsonError(f"local LLM trả HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise InvalidJsonError("local LLM trả body không phải JSON") from exc

    def _record_usage(self, data: dict) -> None:
        usage = data.get("usage") or {}
        self.last_usage = ProviderUsage(
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            est_cost_usd=0.0,
        )

    # --- Bước trích bộ lọc ---

    async def extract(self, text: str, last_filters: dict | None) -> ExtractResult:
        messages = [{"role": "system", "content": EXTRACT_SYSTEM_PROMPT}]
        messages.extend(EXTRACT_FEWSHOT)
        messages.append(
            {"role": "user", "content": self._user_content(text, last_filters)}
        )

        last_error: Exception | None = None
        for attempt in range(2):
            data = await self._post(
                "/chat/completions",
                {
                    "model": self.model,
                    "messages": messages,
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                    "stream": False,
                },
            )
            self._record_usage(data)
            content = self._content_of(data)
            try:
                return ExtractResult.model_validate_json(content)
            except Exception as exc:
                last_error = exc
                if attempt == 1:
                    raise InvalidJsonError(f"JSON không hợp lệ: {exc}") from exc
                messages.append({"role": "assistant", "content": content})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"JSON không hợp lệ: {exc}. "
                            "Trả lại đúng schema JSON, không thêm chữ nào ngoài JSON."
                        ),
                    }
                )
        raise InvalidJsonError(f"JSON không hợp lệ: {last_error}")

    # --- Bước soạn câu trả lời ---

    async def compose(self, text: str, listings: list[dict]) -> AsyncIterator[str]:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": COMPOSE_SYSTEM_PROMPT},
                *COMPOSE_FEWSHOT,
                {"role": "user", "content": self._compose_content(text, listings)},
            ],
            "temperature": 0.3,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream(
                    "POST", url, json=payload, headers=headers
                ) as response:
                    if response.status_code >= 500:
                        raise ProviderError(
                            f"local LLM trả HTTP {response.status_code}"
                        )
                    if response.status_code >= 400:
                        raise InvalidJsonError(
                            f"local LLM trả HTTP {response.status_code}"
                        )
                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        chunk = line[5:].strip()
                        if chunk == "[DONE]":
                            break
                        try:
                            data = json.loads(chunk)
                        except ValueError:
                            continue
                        if data.get("usage"):
                            self._record_usage(data)
                        choices = data.get("choices") or []
                        if not choices:
                            continue
                        delta = (choices[0].get("delta") or {}).get("content")
                        if delta:
                            yield delta
        except httpx.TimeoutException as exc:
            raise ProviderError(f"timeout khi soạn câu trả lời: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"không gọi được local LLM: {exc}") from exc

    # --- helpers ---

    @staticmethod
    def _content_of(data: dict) -> str:
        choices = data.get("choices") or []
        if not choices:
            raise InvalidJsonError("local LLM không trả choices")
        return (choices[0].get("message") or {}).get("content") or ""

    @staticmethod
    def _user_content(text: str, last_filters: dict | None) -> str:
        if not last_filters:
            return text
        return (
            f"Bộ lọc của lượt trước (dùng để hiểu câu hỏi nối tiếp):\n"
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
