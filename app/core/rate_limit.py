"""DB-backed reservations: daily chat turns, concurrent leases and external calls."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.db.models_ai import AiChatLease, AiUsageDaily, AiUserLock
from app.db.session import ai_session_factory

EXTERNAL_BUDGET_USER = "__external_budget__"


class UsageTracker:
    def today(self):
        return datetime.now(ZoneInfo(get_settings().quota_timezone)).date()

    def _ensure_row(self, session, user_id, day):
        # Savepoint preserves the outer transaction when another worker inserted first.
        if session.get(AiUsageDaily, (user_id, day)) is None:
            try:
                with session.begin_nested():
                    session.add(AiUsageDaily(user_id=user_id, usage_date=day))
                    session.flush()
            except IntegrityError:
                pass

    def reserve_chat(self, user_id, request_id):
        settings = get_settings()
        day = self.today()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with ai_session_factory() as session, session.begin():
            # All requests for this user serialize even across local midnight.
            if session.get(AiUserLock, user_id) is None:
                try:
                    with session.begin_nested():
                        session.add(AiUserLock(user_id=user_id))
                        session.flush()
                except IntegrityError:
                    pass
            session.execute(
                update(AiUserLock)
                .where(AiUserLock.user_id == user_id)
                .values(user_id=user_id)
            )
            self._ensure_row(session, user_id, day)
            # UPDATE locks the user/day row; all workers serialize on this row.
            changed = session.execute(
                update(AiUsageDaily)
                .where(
                    AiUsageDaily.user_id == user_id,
                    AiUsageDaily.usage_date == day,
                    AiUsageDaily.request_count < settings.chat_daily_limit_per_user,
                )
                .values(request_count=AiUsageDaily.request_count + 1)
            ).rowcount
            if not changed:
                raise HTTPException(
                    429, "Bạn đã hết lượt chat hôm nay. Vui lòng thử lại vào ngày mai."
                )
            active = session.scalar(
                select(func.count())
                .select_from(AiChatLease)
                .where(AiChatLease.user_id == user_id, AiChatLease.expires_at > now)
            )
            if active >= settings.max_concurrent_per_user:
                raise HTTPException(
                    429, "Bạn đang có nhiều lượt chat. Hãy chờ lượt đang chạy kết thúc."
                )
            session.execute(
                delete(AiChatLease).where(
                    AiChatLease.user_id == user_id, AiChatLease.expires_at <= now
                )
            )
            session.add(
                AiChatLease(
                    request_id=request_id,
                    user_id=user_id,
                    expires_at=now
                    + timedelta(seconds=settings.chat_timeout_seconds + 30),
                )
            )
        # Accepted turns count even if failed/cancelled. Concurrent rejection rolls back.
        return day

    def release_chat(self, request_id):
        with ai_session_factory() as session, session.begin():
            session.execute(
                delete(AiChatLease).where(AiChatLease.request_id == request_id)
            )

    def external_cap_reached_anywhere(self):
        """Reserve one provider call, fail closed when budget storage is unavailable."""
        try:
            day = self.today()
            with ai_session_factory() as session, session.begin():
                self._ensure_row(session, EXTERNAL_BUDGET_USER, day)
                changed = session.execute(
                    update(AiUsageDaily)
                    .where(
                        AiUsageDaily.user_id == EXTERNAL_BUDGET_USER,
                        AiUsageDaily.usage_date == day,
                        AiUsageDaily.external_count < get_settings().external_daily_cap,
                    )
                    .values(external_count=AiUsageDaily.external_count + 1)
                ).rowcount
                return not changed
        except Exception:
            return True

    def record_usage(self, user_id, day, records):
        calls = [r for r in records if r.status not in ("BREAKER_OPEN", "CAP_EXCEEDED")]
        with ai_session_factory() as session, session.begin():
            self._ensure_row(session, user_id, day)
            session.execute(
                update(AiUsageDaily)
                .where(
                    AiUsageDaily.user_id == user_id,
                    AiUsageDaily.usage_date == day,
                )
                .values(
                    local_count=AiUsageDaily.local_count
                    + sum(r.provider == "local" for r in calls),
                    external_count=AiUsageDaily.external_count
                    + sum(r.provider == "external" for r in calls),
                    input_tokens=AiUsageDaily.input_tokens
                    + sum(r.input_tokens or 0 for r in calls),
                    output_tokens=AiUsageDaily.output_tokens
                    + sum(r.output_tokens or 0 for r in calls),
                    est_cost_usd=AiUsageDaily.est_cost_usd
                    + sum((r.est_cost_usd or Decimal(0) for r in calls), Decimal(0)),
                )
            )


usage_tracker = UsageTracker()
