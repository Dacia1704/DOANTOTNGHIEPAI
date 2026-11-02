"""Đánh giá chatbot: chạy bộ câu hỏi mẫu trên từng provider, so sánh độ chính xác.

CÁCH DÙNG:
    python -m scripts.evaluate_chat --provider rules
    python -m scripts.evaluate_chat --provider local --model qwen3:4b

Bộ câu hỏi mẫu: data/chat_eval.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from pathlib import Path

from app.chat.llm.external_provider import ExternalProvider
from app.chat.llm.local_provider import LocalProvider
from app.chat.llm.rules_provider import RulesProvider
from app.chat.schemas import ExtractResult
from app.config import get_settings
from app.db.models_ai import AiEvalRun
from app.db.session import ai_session_factory, core_session_factory

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "chat_eval.jsonl"


def load_cases() -> list[dict]:
    if not DATA_FILE.exists():
        return []
    cases = []
    with DATA_FILE.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def _matches(expected: dict, actual: ExtractResult) -> bool:
    """Kiểm tra bộ lọc thực tế có khớp kỳ vọng không."""
    filters = actual.filters

    # Nếu kỳ vọng chỉ định intent thì chỉ xét intent.
    if "intent" in expected:
        return actual.intent == expected["intent"]

    checks = 0
    passed = 0

    for key in ("price_min", "price_max", "area_min", "area_max"):
        if key not in expected:
            continue
        checks += 1
        if getattr(filters, key) == expected[key]:
            passed += 1

    if "price_max_lower" in expected:
        checks += 1
        if expected["price_max_lower"]:
            if actual.intent == "FOLLOW_UP":
                passed += 1
            elif filters.price_max is not None and (
                filters.price_min is None or filters.price_max < filters.price_min
            ):
                passed += 1

    if "province" in expected:
        checks += 1
        value = (filters.province or "").lower()
        if value and value in expected["province"].lower():
            passed += 1

    if "occupants" in expected:
        checks += 1
        if filters.occupants == expected["occupants"]:
            passed += 1

    if "ward_or_area" in expected:
        checks += 1
        value = (filters.ward_or_area or "").lower()
        if value and value in expected["ward_or_area"].lower():
            passed += 1

    if "area" in expected:
        checks += 1
        value = (filters.ward_or_area or "").lower()
        if value and expected["area"].lower() in value:
            passed += 1

    if "room_type" in expected:
        checks += 1
        value = (filters.room_type or "").lower()
        if value and expected["room_type"].lower() in value:
            passed += 1

    if "amenities" in expected:
        checks += 1
        actual_amenities = {a.lower() for a in filters.amenities}
        wanted = {a.lower() for a in expected["amenities"]}
        if wanted and wanted.issubset(actual_amenities):
            passed += 1

    if checks == 0:
        return True
    return passed == checks


async def run_provider(provider_name: str, model: str | None) -> dict:
    settings = get_settings()

    if provider_name == "rules":
        with core_session_factory() as session:
            from app.chat.service import load_catalogue

            catalogue = load_catalogue(session)
        provider = RulesProvider(catalogue_loader=lambda: catalogue)
    elif provider_name == "local":
        provider = LocalProvider(
            base_url=settings.local_llm_base_url,
            model=model or settings.local_llm_model,
            api_key=settings.local_llm_api_key,
            timeout=settings.local_timeout_seconds,
        )
    else:
        provider = ExternalProvider(
            provider=settings.external_provider,
            model=model or settings.external_model,
            gemini_api_key=settings.gemini_api_key,
            anthropic_api_key=settings.anthropic_api_key,
            timeout=settings.local_timeout_seconds,
        )

    cases = load_cases()
    if not cases:
        return {"error": "Không có data/chat_eval.jsonl"}

    correct = 0
    failures: list[dict] = []
    latencies: list[int] = []

    for case in cases:
        started = time.perf_counter()
        try:
            result = await provider.extract(case["q"], None)
            latency = int((time.perf_counter() - started) * 1000)
            latencies.append(latency)
            if _matches(case.get("expected", {}), result):
                correct += 1
            else:
                failures.append(
                    {
                        "q": case["q"],
                        "expected": case.get("expected"),
                        "actual": result.model_dump(exclude_none=True),
                    }
                )
        except Exception as exc:
            failures.append(
                {"q": case["q"], "error": f"{type(exc).__name__}: {exc}"[:200]}
            )

    latencies.sort()
    return {
        "provider": provider_name,
        "model": getattr(provider, "model", None),
        "total": len(cases),
        "correct": correct,
        "accuracy": round(correct / len(cases), 4),
        "latency_median_ms": latencies[len(latencies) // 2] if latencies else 0,
        "latency_max_ms": latencies[-1] if latencies else 0,
        "failures": failures[:10],
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Đánh giá chatbot")
    parser.add_argument(
        "--provider", choices=["rules", "local", "external"], default="rules"
    )
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    outcome = await run_provider(args.provider, args.model)
    print(json.dumps(outcome, ensure_ascii=False, indent=2))

    if "error" not in outcome:
        with ai_session_factory() as session:
            session.add(
                AiEvalRun(
                    id=str(uuid.uuid4()),
                    kind="CHAT",
                    name=f"{args.provider}:{outcome.get('model') or 'rules'}",
                    config={"provider": args.provider},
                    metrics={
                        "accuracy": outcome["accuracy"],
                        "latency_median_ms": outcome["latency_median_ms"],
                        "total": outcome["total"],
                    },
                    dataset_note="Bộ câu hỏi mẫu data/chat_eval.jsonl (dữ liệu giả)",
                )
            )
            session.commit()
        print("Đã lưu kết quả vào ai_eval_runs.")


if __name__ == "__main__":
    asyncio.run(main())
