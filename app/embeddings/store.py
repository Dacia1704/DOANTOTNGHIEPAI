"""Immutable vector snapshots, swapped atomically after validation."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

import numpy as np
from sqlalchemy import select

from app.db.models_ai import ListingEmbedding
from app.db.session import ai_session_factory


@dataclass(frozen=True)
class Snapshot:
    matrix: np.ndarray
    index: dict[str, int]
    model_name: str | None


class EmbeddingStore:
    def __init__(self):
        self._snapshot = Snapshot(np.zeros((0, 0), dtype=np.float32), {}, None)
        self._lock = Lock()

    def load(self, model_name):
        with ai_session_factory() as session:
            rows = session.execute(
                select(
                    ListingEmbedding.listing_id,
                    ListingEmbedding.embedding,
                    ListingEmbedding.dim,
                ).where(ListingEmbedding.model_name == model_name)
            ).all()
        dims = {r.dim for r in rows}
        if len(dims) > 1 or (dims and min(dims) <= 0):
            raise ValueError("Incompatible embedding dimensions")
        for row in rows:
            if len(row.embedding) != row.dim * 4:
                raise ValueError("Corrupt embedding payload")
        matrix = (
            np.vstack([np.frombuffer(r.embedding, dtype=np.float32) for r in rows])
            if rows
            else np.zeros((0, 0), dtype=np.float32)
        )
        if not np.isfinite(matrix).all():
            raise ValueError("Non-finite embedding")
        matrix.setflags(write=False)
        snapshot = Snapshot(
            matrix, {r.listing_id: i for i, r in enumerate(rows)}, model_name
        )
        with self._lock:
            self._snapshot = snapshot

    def get(self, listing_id):
        snapshot = self._snapshot
        position = snapshot.index.get(listing_id)
        return None if position is None else snapshot.matrix[position]

    def __len__(self):
        return len(self._snapshot.index)


embedding_store = EmbeddingStore()
