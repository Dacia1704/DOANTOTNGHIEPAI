"""Model đọc ghi cho schema riêng rental_ai (Alembic quản lý)."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class AiBase(DeclarativeBase):
    pass


class ListingEmbedding(AiBase):
    __tablename__ = "listing_embeddings"

    listing_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    model_name: Mapped[str] = mapped_column(String(100))
    dim: Mapped[int] = mapped_column(SmallInteger)
    content_hash: Mapped[str] = mapped_column(String(64))
    embedding: Mapped[bytes] = mapped_column(LargeBinary)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class UserRecommendation(AiBase):
    __tablename__ = "user_recommendations"
    __table_args__ = (
        UniqueConstraint("user_id", "listing_id", "model_version", name="uk_ur"),
        Index("ix_ur_user_score", "user_id", "score"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36))
    listing_id: Mapped[str] = mapped_column(String(36))
    score: Mapped[Decimal] = mapped_column(Numeric(6, 4))
    breakdown: Mapped[dict | None] = mapped_column(JSON)
    model_version: Mapped[str] = mapped_column(String(50))
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AiConversation(AiBase):
    __tablename__ = "ai_conversations"
    __table_args__ = (Index("ix_aic_user", "user_id", "updated_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class AiMessage(AiBase):
    __tablename__ = "ai_messages"
    __table_args__ = (
        Index("ix_aim_conv_created", "conversation_id", "created_at"),
        Index("ix_aim_request", "request_id"),
        Index("ix_aim_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("ai_conversations.id", ondelete="CASCADE")
    )
    request_id: Mapped[str] = mapped_column(String(36))
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str | None] = mapped_column(String(20))
    filters_json: Mapped[dict | None] = mapped_column(JSON)
    listing_ids: Mapped[list | None] = mapped_column(JSON)
    extract_provider: Mapped[str | None] = mapped_column(String(20))
    compose_provider: Mapped[str | None] = mapped_column(String(20))
    total_latency_ms: Mapped[int | None] = mapped_column(Integer)
    fallback_reason: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class LlmCallLog(AiBase):
    __tablename__ = "llm_call_logs"
    __table_args__ = (
        Index("ix_llm_request", "request_id"),
        Index("ix_llm_created", "created_at"),
        Index("ix_llm_provider_status", "provider", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36))
    message_id: Mapped[str | None] = mapped_column(String(36))
    user_id: Mapped[str] = mapped_column(String(36))
    step: Mapped[str] = mapped_column(String(20))
    attempt_no: Mapped[int] = mapped_column(SmallInteger, default=1)
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30))
    is_final: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    est_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    error_detail: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RecommendationRequest(AiBase):
    __tablename__ = "recommendation_requests"
    __table_args__ = (Index("ix_rr_user_created", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36))
    source: Mapped[str] = mapped_column(String(20), default="HOME")
    model_version: Mapped[str] = mapped_column(String(50))
    cold_start: Mapped[bool] = mapped_column(Boolean, default=False)
    personalization_used: Mapped[bool] = mapped_column(Boolean, default=True)
    profile_snapshot: Mapped[dict | None] = mapped_column(JSON)
    weights: Mapped[dict | None] = mapped_column(JSON)
    candidate_count: Mapped[int | None] = mapped_column(Integer)
    returned_count: Mapped[int | None] = mapped_column(Integer)
    listing_ids: Mapped[list | None] = mapped_column(JSON)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RecommendationEvent(AiBase):
    __tablename__ = "recommendation_events"
    __table_args__ = (
        Index("ix_re_user_created", "user_id", "created_at"),
        Index("ix_re_request", "request_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36))
    user_id: Mapped[str] = mapped_column(String(36))
    listing_id: Mapped[str] = mapped_column(String(36))
    event_type: Mapped[str] = mapped_column(String(20))
    position: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(20), default="HOME")
    model_version: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AiFeedback(AiBase):
    __tablename__ = "ai_feedback"
    __table_args__ = (UniqueConstraint("message_id", "user_id", name="uk_aif"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    message_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("ai_messages.id", ondelete="CASCADE")
    )
    user_id: Mapped[str] = mapped_column(String(36))
    rating: Mapped[str] = mapped_column(String(10))
    reason: Mapped[str | None] = mapped_column(String(30))
    comment: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AiUsageDaily(AiBase):
    __tablename__ = "ai_usage_daily"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    usage_date: Mapped[date] = mapped_column(Date, primary_key=True)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    local_count: Mapped[int] = mapped_column(Integer, default=0)
    external_count: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    est_cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 4), default=0)


class AiUserLock(AiBase):
    """Stable mutex row, independent of the daily quota boundary."""

    __tablename__ = "ai_user_locks"
    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)


class AiChatLease(AiBase):
    __tablename__ = "ai_chat_leases"
    __table_args__ = (Index("ix_chat_lease_user_expiry", "user_id", "expires_at"),)
    request_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36))
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class EmbeddingSyncRun(AiBase):
    __tablename__ = "embedding_sync_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    model_name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="RUNNING")
    scanned: Mapped[int] = mapped_column(Integer, default=0)
    embedded: Mapped[int] = mapped_column(Integer, default=0)
    unchanged: Mapped[int] = mapped_column(Integer, default=0)
    removed: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String(500))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class AiEvalRun(AiBase):
    __tablename__ = "ai_eval_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(100))
    config: Mapped[dict | None] = mapped_column(JSON)
    metrics: Mapped[dict] = mapped_column(JSON)
    dataset_note: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SyncState(AiBase):
    __tablename__ = "sync_state"

    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
