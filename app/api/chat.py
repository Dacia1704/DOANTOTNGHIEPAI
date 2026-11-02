"""Chat SSE with request-local state and short, thread-owned DB sessions."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from contextlib import aclosing
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError, field_validator
from starlette.background import BackgroundTask

from app.api.deps import get_gateway
from app.chat.llm.gateway import CallContext, Gateway
from app.chat.llm.rules_provider import RulesProvider, compose_template
from app.chat.search import (
    FilterResolutionError,
    fetch_listing_cards,
    resolve_amenity_ids,
    resolve_locations,
)
from app.chat.service import (
    OUT_OF_SCOPE_REPLY,
    apply_profile_defaults_to_filters,
    cards_to_payloads,
    get_or_create_conversation,
    last_filters_of,
    load_catalogue,
    merge_filters,
    profile_defaults,
    validate_extracted,
    ward_name_lookup,
)
from app.config import get_settings
from app.core.deps import get_current_tenant
from app.core.rate_limit import usage_tracker
from app.core.security import AuthenticatedUser
from app.db.models_ai import AiMessage
from app.db.session import ai_session_factory, core_session_factory
from app.observability.request_context import get_request_id

logger = logging.getLogger("ai.api.chat")
router = APIRouter(prefix="/ai", tags=["ai"])


class ChatRequest(BaseModel):
    conversation_id: str | None = Field(default=None, max_length=36)
    message: str = Field(min_length=1, max_length=2000)

    @field_validator("message")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Message cannot be blank")
        return value.strip()


def _sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@dataclass
class Turn:
    request_id: str
    conversation_id: str
    user_id: str
    message: str
    intent: str = "SEARCH"
    status: str = "FAILED"
    parts: list[str] = field(default_factory=list)
    filters: dict = field(default_factory=dict)
    listing_ids: list[str] = field(default_factory=list)
    fallback_reason: str | None = None
    started: float = field(default_factory=time.perf_counter)


def _prepare(body, user_id):
    with ai_session_factory() as session:
        cid = get_or_create_conversation(session, user_id, body.conversation_id)
        previous = last_filters_of(session, cid)
    with core_session_factory() as session:
        return (
            cid,
            previous,
            load_catalogue(session),
            profile_defaults(session, user_id),
            ward_name_lookup(session),
        )


def _search(filters):
    with core_session_factory() as session:
        wards, provinces = resolve_locations(
            session, filters.ward_or_area, filters.province
        )
        amenities, unknown = resolve_amenity_ids(session, filters.amenities)
        if unknown:
            raise FilterResolutionError(
                "Bạn vui lòng nói rõ tiện ích cần tìm: " + ", ".join(unknown)
            )
        cards = fetch_listing_cards(
            session,
            filters,
            ward_ids=wards,
            province_ids=provinces,
            amenity_ids=amenities,
            price_tolerance_pct=0,
            limit=8,
        )
        return cards_to_payloads(cards, session)


def _finish(turn, gateway, day):
    """Called exactly once, including failure/disconnect; no Session crosses threads."""
    records = list(gateway.call_logger.records)
    message_id = str(uuid.uuid4())
    final = {r.step: r.provider for r in records if r.is_final}
    try:
        with ai_session_factory() as session:
            session.add(
                AiMessage(
                    id=str(uuid.uuid4()),
                    conversation_id=turn.conversation_id,
                    request_id=turn.request_id,
                    role="USER",
                    content=turn.message,
                )
            )
            session.add(
                AiMessage(
                    id=message_id,
                    conversation_id=turn.conversation_id,
                    request_id=turn.request_id,
                    role="ASSISTANT",
                    content="".join(turn.parts),
                    status=turn.status,
                    intent=turn.intent,
                    filters_json=turn.filters,
                    listing_ids=turn.listing_ids,
                    extract_provider=final.get("EXTRACT"),
                    compose_provider=final.get("COMPOSE"),
                    fallback_reason=turn.fallback_reason,
                    total_latency_ms=int((time.perf_counter() - turn.started) * 1000),
                )
            )
            session.commit()
            gateway.call_logger.set_message_id(message_id)
            gateway.call_logger.flush(session)
    except Exception:
        message_id = None
        logger.error("Could not persist AI turn request_id=%s", turn.request_id)
    finally:
        try:
            usage_tracker.record_usage(turn.user_id, day, records)
        finally:
            usage_tracker.release_chat(turn.request_id)
    return message_id


async def _turn_events(turn, gateway, previous, catalogue, profile, ward_lookup):
    # The catalogue and provider usage state are owned by this request.
    for provider in gateway.chain:
        if isinstance(provider, RulesProvider):
            provider._catalogue_loader = lambda: catalogue
    result, turn.fallback_reason = await gateway.extract(
        CallContext(turn.request_id, turn.user_id, "EXTRACT"),
        turn.message,
        previous,
        validator=lambda value: validate_extracted(value, None, catalogue),
    )
    turn.intent = result.intent
    if result.intent in ("CLARIFY", "OUT_OF_SCOPE"):
        turn.status = result.intent
        reply = (
            OUT_OF_SCOPE_REPLY
            if result.intent == "OUT_OF_SCOPE"
            else (
                result.clarification_question
                or "Bạn muốn tìm phòng ở khu vực nào và khoảng giá bao nhiêu?"
            )
        )
        turn.parts.append(reply)
        yield _sse("token", {"text": reply})
        return
    explicit_filters = merge_filters(result.filters, previous)
    filters = apply_profile_defaults_to_filters(explicit_filters, profile, ward_lookup)
    payloads = await asyncio.to_thread(_search, filters)
    # Persist only conversational intent; profile defaults are re-applied per turn.
    turn.filters = explicit_filters.model_dump(exclude_none=True)
    turn.listing_ids = [p["id"] for p in payloads]
    yield _sse("listings", {"items": payloads})
    async with aclosing(
        gateway.compose_stream(
            CallContext(turn.request_id, turn.user_id, "COMPOSE"),
            turn.message,
            payloads,
        )
    ) as chunks:
        async for _, chunk in chunks:
            if chunk is None:
                turn.fallback_reason = "provider_failed_mid_stream"
                yield _sse("retry", {"reason": turn.fallback_reason})
                return
            turn.parts.append(chunk)
            yield _sse("token", {"text": chunk})
    if not turn.parts:
        turn.parts.append(compose_template(payloads))
        yield _sse("token", {"text": turn.parts[-1]})
    turn.status = "OK"


@router.post("/chat")
async def chat(
    body: ChatRequest,
    user: AuthenticatedUser = Depends(get_current_tenant),
    gateway: Gateway = Depends(get_gateway),
):
    # Internal UUID is never controlled by the client correlation header.
    request_id = get_request_id() or str(uuid.uuid4())
    prepared = await asyncio.to_thread(_prepare, body, user.id)
    day = await asyncio.to_thread(usage_tracker.reserve_chat, user.id, request_id)
    cid, previous, catalogue, profile, wards = prepared
    turn = Turn(request_id, cid, user.id, body.message)

    async def events():
        message_id = None
        try:
            yield _sse("meta", {"request_id": request_id, "conversation_id": cid})
            async with asyncio.timeout(get_settings().chat_timeout_seconds):
                async with aclosing(
                    _turn_events(turn, gateway, previous, catalogue, profile, wards)
                ) as stream:
                    async for event in stream:
                        yield event
        except (ValidationError, FilterResolutionError) as exc:
            turn.status = "CLARIFY"
            turn.intent = "CLARIFY"
            question = (
                str(exc)
                if isinstance(exc, FilterResolutionError)
                else (
                    "Các điều kiện đang mâu thuẫn. Bạn cho biết lại khoảng giá và diện tích cần tìm nhé?"
                )
            )
            turn.parts.append(question)
            yield _sse("token", {"text": question})
        except asyncio.CancelledError:
            turn.status = "FAILED"
            turn.fallback_reason = "client_disconnected"
            raise
        except Exception as exc:
            turn.status = "FAILED"
            turn.fallback_reason = type(exc).__name__
            logger.warning(
                "Chat failed request_id=%s error=%s", request_id, type(exc).__name__
            )
        finally:
            try:
                message_id = await asyncio.shield(
                    asyncio.to_thread(_finish, turn, gateway, day)
                )
            except Exception:
                logger.error("Chat finalization failed request_id=%s", request_id)
        yield _sse(
            "done",
            {
                "status": turn.status,
                "message_id": message_id,
                "request_id": request_id,
                "conversation_id": cid,
            },
        )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        background=BackgroundTask(usage_tracker.release_chat, request_id),
    )
