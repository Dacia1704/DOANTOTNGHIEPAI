"""Tầng local: Ollama (hoặc vLLM) qua endpoint OpenAI-compatible."""

from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from app.chat.llm.base import InvalidJsonError, ProviderError, ProviderUsage
from app.chat.prompts import (
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
        messages = [{
            "role": "system",
            "content": EXTRACT_SYSTEM_PROMPT + (
                "\nTrả JSON gọn, bỏ các trường null và mảng rỗng trong filters. "
                "Phải giữ đủ mọi điều kiện được nhắc, nhất là giá và số người."
            ),
        }]
        # Short examples keep CPU prompt evaluation small; the full schema stays above.
        for example in EXTRACT_FEWSHOT:
            if example["role"] == "assistant":
                data = json.loads(example["content"])
                data["filters"] = {
                    key: value for key, value in data["filters"].items()
                    if value is not None and value != []
                }
                if data.get("clarification_question") is None:
                    data.pop("clarification_question", None)
                messages.append({"role": "assistant", "content": json.dumps(
                    data, ensure_ascii=False, separators=(",", ":")
                )})
            else:
                messages.append(example)
        messages.append(
            {"role": "user", "content": self._user_content(text, last_filters)}
        )

        last_error: Exception | None = None
        for attempt in range(2):
            data = await self._post(
                "/chat/completions",
                {
                    "model": self.model,
                    "messages": self._without_thinking(messages),
                    "temperature": 0,
                    **self._generation_options(max_tokens=512),
                    "response_format": {"type": "json_object"},
                    "stream": False,
                },
            )
            self._record_usage(data)
            content = self._content_of(data)
            try:
                result = ExtractResult.model_validate_json(content)
                if result.intent == "SEARCH" and result.filters.is_empty() and not last_filters:
                    result.intent = "CLARIFY"
                    result.clarification_question = (
                        "Bạn muốn tìm phòng ở khu vực nào và khoảng giá bao nhiêu?"
                    )
                return result
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
            "messages": self._without_thinking([
                {"role": "system", "content": COMPOSE_SYSTEM_PROMPT + (
                    "\nDanh sách đã được SQL lọc theo yêu cầu, không tự lọc lại. "
                    "Nếu có kết quả, xác nhận tìm thấy phòng phù hợp; "
                    "chỉ nói không tìm thấy khi số kết quả bằng 0. "
                    "Số kết quả là số phòng, không phải số người ở."
                )},
                {"role": "user", "content": self._compose_content(text, listings)},
            ]),
            "temperature": 0.3,
            **self._generation_options(max_tokens=384),
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

    def _supports_thinking_switch(self) -> bool:
        name = self.model.lower()
        return name.split(":", 1)[0] == "qwen3" and "instruct" not in name

    def _without_thinking(self, messages: list[dict]) -> list[dict]:
        if not self._supports_thinking_switch():
            return messages
        # Qwen3's prompt switch also covers templates that ignore reasoning_effort.
        return [
            {**message, "content": message["content"] + "\n/no_think"}
            if message["role"] in ("system", "user") else message
            for message in messages
        ]

    def _generation_options(self, *, max_tokens: int) -> dict:
        options = {"max_tokens": max_tokens}
        # Ollama's OpenAI endpoint maps "none" to thinking=false for Qwen3.
        # Other OpenAI-compatible providers retain their existing reasoning mode.
        if self._supports_thinking_switch():
            options["reasoning_effort"] = "none"
        return options

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
        # All cards still go to the frontend; the short reply only needs a sample.
        for index, item in enumerate(listings[:5], start=1):
            amenities = ", ".join(item.get("amenities") or [])
            lines.append(
                f"{index}. {item.get('title')} - {item.get('price_text')} - "
                f"{item.get('area_text')} - {item.get('ward_text')} - {amenities}"
            )
        block = "\n".join(lines) if lines else "(không có phòng nào)"
        return (
            f"Câu hỏi: {text}\n<listing_data>\n"
            f"Tổng số kết quả: {len(listings)}. Chi tiết tối đa 5 kết quả:\n"
            f"{block}\n</listing_data>"
        )
