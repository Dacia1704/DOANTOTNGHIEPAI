from datetime import datetime, timedelta

import numpy as np
import pytest

from app.db.models_ai import ListingEmbedding
from app.embeddings.store import EmbeddingStore
from app.recommend.user_vector import Interaction
from scripts.evaluate_recsys import split_holdout


def test_snapshot_reload_and_corrupt_payload_keep_previous(factory, monkeypatch):
    import app.embeddings.store as module

    monkeypatch.setattr(module, "ai_session_factory", factory)
    store = EmbeddingStore()
    with factory() as s:
        s.add(
            ListingEmbedding(
                listing_id="one",
                model_name="test",
                dim=2,
                content_hash="hash",
                embedding=np.array([1.0, 0.0], dtype=np.float32).tobytes(),
            )
        )
        s.commit()
    store.load("test")
    assert np.array_equal(store.get("one"), [1.0, 0.0])
    with factory() as s:
        row = s.get(ListingEmbedding, "one")
        row.embedding = np.array([0.0, 1.0], dtype=np.float32).tobytes()
        s.commit()
    store.load("test")
    assert np.array_equal(store.get("one"), [0.0, 1.0])
    with factory() as s:
        s.get(ListingEmbedding, "one").embedding = b"bad"
        s.commit()
    with pytest.raises(ValueError):
        store.load("test")
    assert np.array_equal(store.get("one"), [0.0, 1.0])
    with pytest.raises(ValueError):
        store.get("one")[0] = 123


def test_holdout_excludes_target_and_future_interactions():
    now = datetime.now()
    held, train = split_holdout(
        [
            Interaction("target", "VIEW", now - timedelta(days=3)),
            Interaction("history", "SAVE", now - timedelta(days=2)),
            Interaction("target", "CONTACT", now - timedelta(days=1)),
            Interaction("future", "VIEW", now),
        ]
    )
    assert held.listing_id == "target"
    assert [i.listing_id for i in train] == ["history"]
