from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core import rate_limit
from app.db.models_ai import AiBase, AiUsageDaily


def test_independent_connections_share_daily_quota_and_concurrent_limit(
    tmp_path, monkeypatch
):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'quotas.sqlite'}",
        connect_args={"check_same_thread": False, "timeout": 20},
    )
    AiBase.metadata.create_all(engine)
    factory = sessionmaker(engine)
    monkeypatch.setattr(rate_limit, "ai_session_factory", factory)
    monkeypatch.setattr(
        rate_limit,
        "get_settings",
        lambda: SimpleNamespace(
            quota_timezone="UTC",
            chat_daily_limit_per_user=5,
            max_concurrent_per_user=3,
            chat_timeout_seconds=120,
            external_daily_cap=2,
        ),
    )
    tracker = rate_limit.UsageTracker()

    def attempt(index):
        try:
            tracker.reserve_chat("user", str(index))
            return str(index)
        except HTTPException as exc:
            assert exc.status_code == 429
            return None

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            accepted = [rid for rid in pool.map(attempt, range(8)) if rid is not None]
        assert len(accepted) == 3
        for rid in accepted:
            tracker.release_chat(rid)
        with ThreadPoolExecutor(max_workers=8) as pool:
            accepted = [
                rid for rid in pool.map(attempt, range(8, 16)) if rid is not None
            ]
        assert len(accepted) == 2
        with factory() as s:
            assert (
                s.scalar(
                    select(AiUsageDaily).where(AiUsageDaily.user_id == "user")
                ).request_count
                == 5
            )
    finally:
        engine.dispose()
