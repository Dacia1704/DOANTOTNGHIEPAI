"""Gom dòng llm_call_logs trong bộ nhớ, ghi bằng background task.

Việc ghi log không được làm hỏng request: nếu ghi lỗi thì chỉ ghi cảnh báo
vào log ứng dụng, không trả lỗi cho user.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy.orm import Session

logger = logging.getLogger("ai.call_logs")


@dataclass
class CallRecord:
    request_id: str
    user_id: str
    step: str
    attempt_no: int
    provider: str
    model: str | None
    status: str
    is_final: bool = False
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    est_cost_usd: Decimal | None = None
    error_detail: str | None = None
    message_id: str | None = None


@dataclass
class CallLogger:
    """Bộ đệm dòng log trong bộ nhớ, flush bằng background task."""

    records: list[CallRecord] = field(default_factory=list)

    def add(self, record: CallRecord) -> None:
        self.records.append(record)

    def set_message_id(self, message_id: str) -> None:
        for record in self.records:
            if record.message_id is None:
                record.message_id = message_id

    def flush(self, session: Session) -> None:
        if not self.records:
            return
        from app.db.models_ai import LlmCallLog

        pending, self.records = self.records, []
        try:
            for record in pending:
                session.add(
                    LlmCallLog(
                        id=str(uuid.uuid4()),
                        request_id=record.request_id,
                        message_id=record.message_id,
                        user_id=record.user_id,
                        step=record.step,
                        attempt_no=record.attempt_no,
                        provider=record.provider,
                        model=record.model,
                        status=record.status,
                        is_final=record.is_final,
                        latency_ms=record.latency_ms,
                        input_tokens=record.input_tokens,
                        output_tokens=record.output_tokens,
                        est_cost_usd=record.est_cost_usd,
                        error_detail=(record.error_detail or "")[:255] or None,
                    )
                )
            session.commit()
        except Exception:
            session.rollback()
            logger.warning("Không ghi được llm_call_logs", exc_info=True)
