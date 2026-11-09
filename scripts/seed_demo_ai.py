"""Seed labelled AI fixtures from existing core users/listings; never writes rental_db."""

import argparse
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert

from app.chat.schemas import SearchFilters
from app.chat.search import fetch_listing_cards
from app.chat.service import cards_to_payloads
from app.db.models_ai import AiBase
from app.db.models_core import CoreRole, CoreUser, CoreUserRole
from app.db.session import ai_session_factory, core_session_factory

VERSION = "demo-fixture-v1"


def uid(key):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "rental-ai-demo/" + key))


def build_rows(users, cards, now):
    rows = {name: [] for name in AiBase.metadata.tables
            if name not in ("listing_embeddings", "embedding_sync_runs")}
    created = now - timedelta(days=2)
    for index, user in enumerate(users):
        chosen = [cards[(index + offset) % len(cards)] for offset in range(min(6, len(cards)))]
        listing_ids = [card["id"] for card in chosen]
        card = chosen[0]
        request, conversation, message = (uid(f"{part}/{user}") for part in ("request", "conversation", "assistant"))
        rec_request = uid("recommendation/" + user)
        filters = {"price_max": int(card["price"]), "ward_or_area": card["ward_name"]}
        rows["ai_conversations"].append(dict(id=conversation, user_id=user, created_at=created, updated_at=created))
        rows["ai_messages"].extend([
            dict(id=uid("user-message/" + user), conversation_id=conversation, request_id=request,
                 role="USER", content=f"[Demo] Tìm phòng ở {card['ward_name']} dưới {int(card['price'])} đồng.",
                 created_at=created),
            dict(id=message, conversation_id=conversation, request_id=request, role="ASSISTANT",
                 content=f"[Demo] {card['title']}: {card['price_text']}, {card['area_text']}.",
                 intent="SEARCH", status="OK", filters_json=filters, listing_ids=[card["id"]],
                 extract_provider="rules", compose_provider="rules", total_latency_ms=0,
                 fallback_reason="demo_fixture", created_at=created),
        ])
        for step in ("EXTRACT", "COMPOSE"):
            rows["llm_call_logs"].append(dict(id=uid(f"call/{user}/{step}"), request_id=request,
                message_id=message, user_id=user, step=step, attempt_no=1, provider="rules", model=VERSION,
                status="SUCCESS", is_final=True, latency_ms=0, input_tokens=0, output_tokens=0,
                est_cost_usd=0, error_detail="Demo fixture; no model was called", created_at=created))
        rows["ai_feedback"].append(dict(id=uid("feedback/" + user), message_id=message, user_id=user,
            rating="UP" if index % 5 else "DOWN", reason=None if index % 5 else "OTHER",
            comment="Dữ liệu đánh giá mẫu, không phải phản hồi người dùng thật.", created_at=created))
        rows["ai_usage_daily"].append(dict(user_id=user, usage_date=created.date(), request_count=1,
            local_count=0, external_count=0, input_tokens=0, output_tokens=0, est_cost_usd=0))
        rows["ai_user_locks"].append(dict(user_id=user))
        rows["recommendation_requests"].append(dict(id=rec_request, user_id=user, source="HOME",
            model_version=VERSION, cold_start=True, personalization_used=False,
            profile_snapshot={"fixture": True}, weights={"fixture": True},
            candidate_count=len(chosen), returned_count=len(chosen), listing_ids=listing_ids,
            latency_ms=0, created_at=created))
        for rank, item in enumerate(chosen):
            rows["user_recommendations"].append(dict(id=uid(f"cached/{user}/{item['id']}"),
                user_id=user, listing_id=item["id"], score=round(0.95 - rank * 0.05, 4),
                breakdown={"fixture": True}, model_version=VERSION, generated_at=created))
            rows["recommendation_events"].append(dict(id=uid(f"impression/{user}/{item['id']}"),
                request_id=rec_request, user_id=user, listing_id=item["id"], event_type="IMPRESSION",
                position=rank + 1, source="HOME", model_version=VERSION, created_at=created))
        rows["recommendation_events"].append(dict(id=uid("click/" + user), request_id=rec_request,
            user_id=user, listing_id=card["id"], event_type="CLICK", position=1, source="HOME",
            model_version=VERSION, created_at=created + timedelta(seconds=10)))
    rows["ai_chat_leases"].append(dict(request_id=uid("expired-lease"), user_id=users[0],
                                      expires_at=now - timedelta(hours=3)))
    rows["ai_eval_runs"].append(dict(id=uid("eval-fixture"), kind="FIXTURE", name=VERSION,
        config={"fixture": True}, metrics={"fixture": True, "users": len(users)},
        dataset_note="Synthetic UI fixture only; no model quality measurement.", created_at=created))
    rows["sync_state"].append(dict(name="demo_seed", value=VERSION, updated_at=now))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--users", type=int, default=1000)
    args = parser.parse_args()
    if args.users < 1:
        parser.error("--users must be positive")
    with core_session_factory() as core:
        users = list(core.scalars(select(CoreUser.id).join(CoreUserRole, CoreUserRole.user_id == CoreUser.id)
            .join(CoreRole, CoreRole.id == CoreUserRole.role_id)
            .where(CoreRole.code == "TENANT", CoreUser.status == "ACTIVE")
            .order_by(CoreUser.id).distinct().limit(args.users)))
        cards = cards_to_payloads(fetch_listing_cards(core, SearchFilters(), limit=100), core)
    if not users or not cards:
        raise SystemExit("Seed rental_db first: need TENANT users and public available listings.")
    rows = build_rows(users, cards, datetime.utcnow())
    with ai_session_factory() as session:
        for table in AiBase.metadata.sorted_tables:
            entries = rows.get(table.name, [])
            for offset in range(0, len(entries), 200):
                # Fill nullable fields so heterogeneous user/assistant rows batch consistently.
                batch = [{column.name: entry.get(column.name) for column in table.columns}
                         for entry in entries[offset:offset + 200]]
                statement = insert(table).values(batch)
                update = {column.name: statement.inserted[column.name] for column in table.columns
                          if not column.primary_key}
                if update:
                    session.execute(statement.on_duplicate_key_update(**update))
                else:
                    key = next(iter(table.primary_key.columns)).name
                    session.execute(statement.on_duplicate_key_update(**{key: statement.inserted[key]}))
        session.commit()
    for table, entries in rows.items():
        print(f"{table}: {len(entries)} demo rows")
    print("Build real vectors next: python -m scripts.build_embeddings")


if __name__ == "__main__":
    main()
