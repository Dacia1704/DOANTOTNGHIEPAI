"""Vector sở thích của user, dựng từ hành vi (xem / lưu / liên hệ)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models_core import (
    ListingContactEvent,
    ListingViewHistory,
    SavedListing,
)

# Hệ số hành vi: xem = 1, lưu = 3, liên hệ = 5.
BEHAVIOR_WEIGHTS = {"VIEW": 1.0, "SAVE": 3.0, "CONTACT": 5.0}

# Quy mô suy giảm theo số ngày.
DECAY_DAYS = 14


@dataclass
class Interaction:
    listing_id: str
    kind: str
    created_at: datetime


def load_interactions(session: Session, user_id: str, limit: int = 500) -> list[Interaction]:
    """Gộp 3 loại hành vi thành danh sách có trọng số theo thời gian."""
    interactions: list[Interaction] = []

    rows = session.execute(
        select(ListingViewHistory.listing_id, ListingViewHistory.created_at)
        .where(ListingViewHistory.tenant_id == user_id)
        .order_by(ListingViewHistory.created_at.desc())
        .limit(limit)
    ).all()
    interactions.extend(Interaction(r[0], "VIEW", r[1]) for r in rows)

    rows = session.execute(
        select(SavedListing.listing_id, SavedListing.created_at)
        .where(SavedListing.tenant_id == user_id)
        .order_by(SavedListing.created_at.desc())
        .limit(limit)
    ).all()
    interactions.extend(Interaction(r[0], "SAVE", r[1]) for r in rows)

    rows = session.execute(
        select(ListingContactEvent.listing_id, ListingContactEvent.created_at)
        .where(ListingContactEvent.tenant_id == user_id)
        .order_by(ListingContactEvent.created_at.desc())
        .limit(limit)
    ).all()
    interactions.extend(Interaction(r[0], "CONTACT", r[1]) for r in rows)

    return interactions


def build_user_vector(
    interactions: list[Interaction],
    store,
    now: datetime,
) -> tuple[np.ndarray | None, set[str]]:
    """Trung bình có trọng số của vector các tin đã tương tác, rồi chuẩn hóa.

    Trả về (vector, listing_ids đã dùng).
    """
    vectors: list[np.ndarray] = []
    weights: list[float] = []
    used: set[str] = set()

    for interaction in interactions:
        vector = store.get(interaction.listing_id)
        if vector is None:
            continue
        age_days = max((now - interaction.created_at).days, 0)
        weight = BEHAVIOR_WEIGHTS[interaction.kind] * float(np.exp(-age_days / DECAY_DAYS))
        vectors.append(np.asarray(vector, dtype=np.float32))
        weights.append(weight)
        used.add(interaction.listing_id)

    if not vectors:
        return None, used

    matrix = np.vstack(vectors)
    weight_array = np.asarray(weights, dtype=np.float32)
    result = (matrix * weight_array[:, None]).sum(axis=0)
    norm = float(np.linalg.norm(result))
    if norm == 0:
        return None, used
    return (result / norm).astype(np.float32), used
