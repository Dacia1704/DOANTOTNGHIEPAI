from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from app.api.events import EventRequest, FeedbackRequest, record_event, record_feedback
from app.chat.schemas import SearchFilters
from app.chat.service import merge_filters
from app.core.rate_limit import UsageTracker
from app.core.security import AuthenticatedUser
from app.db.models_ai import (
    AiChatLease,
    AiConversation,
    AiMessage,
    AiUsageDaily,
    RecommendationRequest,
)

USER = AuthenticatedUser("owner", 0, ("ROLE_TENANT",))


def test_feedback_and_event_ownership(factory):
    with factory() as s:
        s.add(AiConversation(id="conv", user_id="owner"))
        s.flush()
        s.add(
            AiMessage(
                id="message",
                conversation_id="conv",
                request_id="chatreq",
                role="ASSISTANT",
                content="reply",
                listing_ids=["l"],
            )
        )
        s.add(
            RecommendationRequest(
                id="req",
                user_id="owner",
                source="HOME",
                model_version="test",
                listing_ids=["l"],
            )
        )
        s.commit()
        stranger = AuthenticatedUser("stranger", 0, ("ROLE_TENANT",))
        for action in [
            lambda: record_feedback(
                FeedbackRequest(message_id="message", rating="UP"), stranger, s
            ),
            lambda: record_event(
                EventRequest(request_id="req", listing_id="l", event_type="CLICK"),
                stranger,
                s,
            ),
            lambda: record_event(
                EventRequest(request_id="req", listing_id="other", event_type="CLICK"),
                USER,
                s,
            ),
        ]:
            with pytest.raises(HTTPException) as exc:
                action()
            assert exc.value.status_code == 404
        assert record_feedback(
            FeedbackRequest(message_id="message", rating="UP"), USER, s
        ) == {"ok": True}
        event = EventRequest(request_id="req", listing_id="l", event_type="CLICK")
        assert (
            record_event(event, USER, s) == record_event(event, USER, s) == {"ok": True}
        )


def test_merge_revalidates_and_keeps_previous_amenities():
    with pytest.raises(ValidationError):
        merge_filters(SearchFilters(price_max=3000000), {"price_min": 4000000})
    assert merge_filters(
        SearchFilters(amenities=["Wifi"]), {"amenities": ["AC"]}
    ).amenities == ["AC", "Wifi"]


def test_daily_and_concurrent_reservations(factory, monkeypatch):
    import app.core.rate_limit as limits

    monkeypatch.setattr(limits, "ai_session_factory", factory)
    monkeypatch.setattr(
        limits,
        "get_settings",
        lambda: SimpleNamespace(
            quota_timezone="UTC",
            chat_daily_limit_per_user=2,
            max_concurrent_per_user=1,
            chat_timeout_seconds=120,
            external_daily_cap=2,
        ),
    )
    tracker = UsageTracker()
    tracker.reserve_chat("owner", "first")
    with pytest.raises(HTTPException):
        tracker.reserve_chat("owner", "parallel")
    tracker.release_chat("first")
    tracker.reserve_chat("owner", "second")
    tracker.release_chat("second")
    with pytest.raises(HTTPException):
        tracker.reserve_chat("owner", "over-quota")
    assert [tracker.external_cap_reached_anywhere() for _ in range(3)] == [
        False,
        False,
        True,
    ]
    with factory() as s:
        assert s.get(AiUsageDaily, ("owner", tracker.today())).request_count == 2
        assert not list(s.scalars(select(AiChatLease)))


def test_purge_keeps_recent_messages_and_dry_run(factory):
    from scripts.purge_logs import purge_session

    now = datetime.utcnow()
    with factory() as s:
        s.add(
            AiConversation(
                id="conv", user_id="owner", created_at=now - timedelta(days=150)
            )
        )
        s.flush()
        for mid, age in [("old", 100), ("new", 1)]:
            s.add(
                AiMessage(
                    id=mid,
                    conversation_id="conv",
                    request_id=mid,
                    role="USER",
                    content="x",
                    created_at=now - timedelta(days=age),
                )
            )
        s.commit()
        purge_session(s, {"owner"}, now)
        s.rollback()
        assert s.get(AiMessage, "old") is not None
        purge_session(s, {"owner"}, now)
        s.commit()
        assert s.get(AiMessage, "old") is None
        assert s.get(AiMessage, "new") is not None
        assert s.get(AiConversation, "conv") is not None
