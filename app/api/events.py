"""POST /ai/events va POST /ai/feedback."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.deps import get_current_tenant
from app.core.security import AuthenticatedUser
from app.db.models_ai import (
    AiConversation,
    AiFeedback,
    AiMessage,
    RecommendationEvent,
    RecommendationRequest,
)
from app.db.session import get_ai_session

router = APIRouter(prefix="/ai", tags=["ai"])


class EventRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=36)
    listing_id: str = Field(min_length=1, max_length=36)
    event_type: str = Field(pattern="^(IMPRESSION|CLICK|SAVE|CONTACT)$")
    position: int | None = Field(default=None, ge=1)
    source: str = Field(default="HOME", pattern="^(HOME|SEARCH|CHATBOT)$")
    model_version: str | None = Field(default=None, max_length=50)


class FeedbackRequest(BaseModel):
    message_id: str = Field(min_length=1, max_length=36)
    rating: str = Field(pattern="^(UP|DOWN)$")
    reason: str | None = Field(
        default=None, pattern="^(WRONG_FILTER|IRRELEVANT|SLOW|OTHER)$"
    )
    comment: str | None = Field(default=None, max_length=500)


@router.post("/events", status_code=status.HTTP_201_CREATED)
def record_event(
    body: EventRequest,
    user: AuthenticatedUser = Depends(get_current_tenant),
    session: Session = Depends(get_ai_session),
) -> dict:
    if body.source == "CHATBOT":
        message = session.scalar(
            select(AiMessage)
            .join(AiConversation)
            .where(
                AiMessage.request_id == body.request_id,
                AiMessage.role == "ASSISTANT",
                AiConversation.user_id == user.id,
            )
        )
        ids = message.listing_ids if message else None
        model_version = None
    else:
        request = session.get(RecommendationRequest, body.request_id)
        if (
            request is None
            or request.user_id != user.id
            or request.source != body.source
        ):
            raise HTTPException(404, "Không tìm thấy lượt gợi ý")
        ids = request.listing_ids
        model_version = request.model_version
    if not ids or body.listing_id not in ids:
        raise HTTPException(404, "Tin đăng không thuộc kết quả của bạn")
    position = ids.index(body.listing_id) + 1
    if body.position is not None and body.position != position:
        raise HTTPException(422, "Vị trí không khớp kết quả")
    event_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"ai:{user.id}:{body.request_id}:{body.listing_id}:{body.event_type}",
        )
    )
    if session.get(RecommendationEvent, event_id):
        return {"ok": True}
    session.add(
        RecommendationEvent(
            id=event_id,
            request_id=body.request_id,
            user_id=user.id,
            listing_id=body.listing_id,
            event_type=body.event_type,
            position=position,
            source=body.source,
            model_version=model_version,
        )
    )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        if session.get(RecommendationEvent, event_id) is None:
            raise
    return {"ok": True}


@router.post("/feedback", status_code=status.HTTP_201_CREATED)
def record_feedback(
    body: FeedbackRequest,
    user: AuthenticatedUser = Depends(get_current_tenant),
    session: Session = Depends(get_ai_session),
) -> dict:
    message = session.scalar(
        select(AiMessage)
        .join(AiConversation)
        .where(AiMessage.id == body.message_id, AiConversation.user_id == user.id)
    )
    if message is None or message.role != "ASSISTANT":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Không tìm thấy câu trả lời để đánh giá",
        )

    existing = (
        session.query(AiFeedback)
        .filter(AiFeedback.message_id == body.message_id, AiFeedback.user_id == user.id)
        .one_or_none()
    )
    if existing is not None:
        existing.rating = body.rating
        existing.reason = body.reason
        existing.comment = body.comment
    else:
        session.add(
            AiFeedback(
                id=str(uuid.uuid4()),
                message_id=body.message_id,
                user_id=user.id,
                rating=body.rating,
                reason=body.reason,
                comment=body.comment,
            )
        )
    session.commit()
    return {"ok": True}
