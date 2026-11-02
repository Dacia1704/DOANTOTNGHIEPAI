"""Retention maintenance. Feedback is retained no longer than its parent message.

Usage: python -m scripts.purge_logs [--dry-run]
"""

import argparse
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.db.models_ai import (
    AiChatLease,
    AiConversation,
    AiFeedback,
    AiMessage,
    LlmCallLog,
    RecommendationEvent,
    RecommendationRequest,
)
from app.db.models_core import CoreUser
from app.db.session import ai_session_factory, core_session_factory

DAYS_MESSAGES = 90
DAYS_FEEDBACK = DAYS_RECOMMENDATION = 365


def purge_session(session, live_user_ids, now):
    counts = {}
    targets = (
        (
            "ai_messages",
            AiMessage,
            AiMessage.created_at < now - timedelta(days=DAYS_MESSAGES),
        ),
        (
            "llm_call_logs",
            LlmCallLog,
            LlmCallLog.created_at < now - timedelta(days=DAYS_MESSAGES),
        ),
        (
            "ai_feedback",
            AiFeedback,
            AiFeedback.created_at < now - timedelta(days=DAYS_FEEDBACK),
        ),
        (
            "recommendation_requests",
            RecommendationRequest,
            RecommendationRequest.created_at
            < now - timedelta(days=DAYS_RECOMMENDATION),
        ),
        (
            "recommendation_events",
            RecommendationEvent,
            RecommendationEvent.created_at < now - timedelta(days=DAYS_RECOMMENDATION),
        ),
        ("ai_chat_leases", AiChatLease, AiChatLease.expires_at <= now),
    )
    for name, model, condition in targets:
        counts[name] = session.execute(delete(model).where(condition)).rowcount
    # FK CASCADE removes messages/feedback for deleted users. Never read AiMessage.user_id.
    for model in (
        AiConversation,
        LlmCallLog,
        RecommendationRequest,
        RecommendationEvent,
        AiFeedback,
    ):
        condition = model.user_id.not_in(live_user_ids) if live_user_ids else True
        counts[model.__tablename__ + "_orphans"] = session.execute(
            delete(model).where(condition)
        ).rowcount
    # An old message alone is not sufficient reason to delete its conversation.
    has_messages = (
        select(AiMessage.id)
        .where(AiMessage.conversation_id == AiConversation.id)
        .exists()
    )
    counts["ai_conversations"] = session.execute(
        delete(AiConversation).where(
            ~has_messages,
            AiConversation.created_at < now - timedelta(days=DAYS_MESSAGES),
        )
    ).rowcount
    return counts


def purge(dry_run=False):
    with core_session_factory() as core:
        live = set(core.scalars(select(CoreUser.id)))
    with ai_session_factory() as session:
        counts = purge_session(
            session, live, datetime.now(timezone.utc).replace(tzinfo=None)
        )
        if dry_run:
            session.rollback()
        else:
            session.commit()
        return counts


def main():
    parser = argparse.ArgumentParser(description="Purge expired AI logs")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    for name, count in purge(args.dry_run).items():
        print(f"{name}: {count}")
    print("Rolled back (dry-run)." if args.dry_run else "Committed.")


if __name__ == "__main__":
    main()
