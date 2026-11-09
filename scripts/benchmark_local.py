"""Đo adapter local trực tiếp, không dùng DB hoặc fallback."""

import asyncio
import time

import httpx

from app.chat.llm.local_provider import LocalProvider
from app.chat.normalize import normalize
from app.config import get_settings


async def main():
    settings = get_settings()
    provider = LocalProvider(
        settings.local_llm_base_url, settings.local_llm_model,
        settings.local_llm_api_key, timeout=settings.local_timeout_seconds,
    )
    # Measure cold loading separately, rather than treating it as extraction time.
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=180) as client:
        response = await client.post(
            f"{provider.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {provider.api_key}"},
            json={"model": provider.model, "messages": [{
                "role": "user", "content": "Reply OK."
            }], "stream": False, **provider._generation_options(max_tokens=8)},
        )
        response.raise_for_status()
    print(f"WARMUP {time.perf_counter()-started:.2f}s", flush=True)
    cases = [
        ("Tìm phòng dưới 3 triệu cho 2 người", None,
         {"price_max": 3000000, "occupants": 2}, "SEARCH"),
        ("Tìm phòng từ 2 đến 4 triệu, diện tích 25m2, có WiFi ở Cầu Giấy", None,
         {"price_min": 2000000, "price_max": 4000000,
          "ward_or_area": "Cầu Giấy", "amenities": ["WiFi"]}, "SEARCH"),
        ("Giảm xuống dưới 2 triệu, cho 3 người", {"price_max": 3000000},
         {"price_max": 2000000, "occupants": 3}, "FOLLOW_UP"),
        ("Thời tiết hôm nay thế nào?", None, {}, "OUT_OF_SCOPE"),
        ("Tôi muốn tìm phòng", None, {}, "CLARIFY"),
    ]
    failures = 0
    for text, previous, expected, intent in cases:
        started = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                provider.extract(text, previous), settings.local_timeout_seconds
            )
            actual = result.filters.model_dump()
            ok = result.intent == intent and all(
                actual.get(key) == value for key, value in expected.items()
            )
            failures += not ok
            print(f"EXTRACT {'PASS' if ok else 'FAIL'} {time.perf_counter()-started:.2f}s",
                  result.model_dump_json(), flush=True)
        except Exception as exc:
            failures += 1
            print(f"EXTRACT ERROR {time.perf_counter()-started:.2f}s {type(exc).__name__}",
                  flush=True)
    started = time.perf_counter()
    first_token = None
    parts = []
    cards = [{
        "title": "Phòng trọ Cầu Giấy", "price_text": "2.5 triệu/tháng",
        "area_text": "25 m2", "ward_text": "Cầu Giấy", "amenities": ["WiFi"],
    }]
    try:
        async with asyncio.timeout(settings.local_timeout_seconds):
            async for part in provider.compose(cases[0][0], cards):
                if first_token is None:
                    first_token = time.perf_counter() - started
                parts.append(part)
        print(f"COMPOSE first_token={first_token} total={time.perf_counter()-started:.2f}s",
              "".join(parts), flush=True)
        reply = "".join(parts)
        ok = (
            bool(reply) and "2.5" in reply.replace(",", ".")
            and "<think>" not in reply
            and "khong tim thay" not in normalize(reply)
            and "chua tim thay" not in normalize(reply)
        )
        failures += not ok
    except Exception as exc:
        failures += 1
        print(f"COMPOSE ERROR {type(exc).__name__}", flush=True)
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    asyncio.run(main())
