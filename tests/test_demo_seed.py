from datetime import datetime

from app.db.models_ai import AiBase
from scripts.seed_demo_ai import build_rows


def test_demo_ai_fixtures_have_owned_results_expired_leases_and_no_fake_model_calls():
    now = datetime(2026, 11, 5, 12)
    cards = [dict(id="room-a", price=2500000, ward_name="Cầu Giấy", title="Demo",
                  price_text="2.5 triệu/tháng", area_text="25 m2")]
    rows = build_rows(["tenant-a", "tenant-b"], cards, now)
    assert set(rows) | {"listing_embeddings", "embedding_sync_runs"} == set(AiBase.metadata.tables)
    assert all(rows.values())
    requests = {row["id"]: row for row in rows["recommendation_requests"]}
    for event in rows["recommendation_events"]:
        parent = requests[event["request_id"]]
        assert event["user_id"] == parent["user_id"]
        assert parent["listing_ids"][event["position"] - 1] == event["listing_id"]
    assert rows["ai_chat_leases"][0]["expires_at"] < now
    assert all(row["usage_date"] < now.date() for row in rows["ai_usage_daily"])
    assert all(row["provider"] == "rules" and row["est_cost_usd"] == 0 for row in rows["llm_call_logs"])
