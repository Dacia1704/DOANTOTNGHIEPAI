"""Job đồng bộ embedding: tính content_hash trên dữ liệu đã join, embed lại khi hash đổi."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

import numpy as np
from sqlalchemy import select

from app.db.models_ai import EmbeddingSyncRun, ListingEmbedding
from app.db.session import ai_session_factory, core_session_factory
from app.embeddings.model import get_embedding_model
from app.embeddings.store import embedding_store
from app.embeddings.text_builder import build_text, content_hash

logger = logging.getLogger("ai.embeddings.sync")


def _fetch_published_listings() -> list[dict]:
    from app.db.inventory import amenities_for_rooms, inventory_query, ordered_inventory

    with core_session_factory() as session:
        rows = session.execute(ordered_inventory(inventory_query())).all()
        amenities = amenities_for_rooms(session, rows)
    by_listing = {}
    for row in rows:
        text = build_text(
            title=row.title,
            description=row.description,
            ward_name=row.ward_name,
            province_name=row.province_name,
            room_type_name=row.room_type_name,
            amenities=sorted(amenities[row.room_id].values()),
        )
        by_listing.setdefault(row.listing_id, []).append(text)
    return [
        {"listing_id": lid, "text": text, "hash": content_hash(text)}
        for lid, parts in by_listing.items()
        for text in [" || ".join(sorted(set(parts)))]
    ]


def sync_embeddings(force: bool = False) -> EmbeddingSyncRun:
    """Đồng bộ embedding. Trả về dòng log kết quả."""
    model = get_embedding_model()
    model.load()
    run_id = str(uuid.uuid4())

    with ai_session_factory() as ai_session:
        run = EmbeddingSyncRun(
            id=run_id,
            model_name=model.name,
            status="RUNNING",
        )
        ai_session.add(run)
        ai_session.commit()

        try:
            items = _fetch_published_listings()
            existing = {
                listing_id: (hash_value, dim)
                for listing_id, hash_value, dim in ai_session.execute(
                    select(
                        ListingEmbedding.listing_id,
                        ListingEmbedding.content_hash,
                        ListingEmbedding.dim,
                    ).where(ListingEmbedding.model_name == model.name)
                ).all()
            }

            live_ids = {item["listing_id"] for item in items}
            removed = 0
            for listing_id in existing:
                if listing_id not in live_ids:
                    ai_session.delete(ai_session.get(ListingEmbedding, listing_id))
                    removed += 1

            to_embed = [
                item
                for item in items
                if force
                or item["listing_id"] not in existing
                or existing[item["listing_id"]][0] != item["hash"]
            ]
            unchanged = len(items) - len(to_embed)

            embedded = 0
            if to_embed:
                vectors = model.encode([item["text"] for item in to_embed])
                for item, vector in zip(to_embed, vectors):
                    payload = np.asarray(vector, dtype=np.float32).tobytes()
                    row = ai_session.get(ListingEmbedding, item["listing_id"])
                    if row is None:
                        ai_session.add(
                            ListingEmbedding(
                                listing_id=item["listing_id"],
                                model_name=model.name,
                                dim=model.dim,
                                content_hash=item["hash"],
                                embedding=payload,
                            )
                        )
                    else:
                        row.model_name = model.name
                        row.dim = model.dim
                        row.content_hash = item["hash"]
                        row.embedding = payload
                    embedded += 1

            ai_session.commit()

            run.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
            run.status = "SUCCESS"
            run.scanned = len(items)
            run.embedded = embedded
            run.unchanged = unchanged
            run.removed = removed
            ai_session.commit()
            logger.info(
                "Đồng bộ embedding: quét %d, mới %d, không đổi %d, gỡ %d",
                len(items),
                embedded,
                unchanged,
                removed,
            )
            embedding_store.load(model.name)
            return run
        except Exception as exc:
            ai_session.rollback()
            run = ai_session.get(EmbeddingSyncRun, run_id)
            if run is not None:
                run.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
                run.status = "FAILED"
                run.error = str(exc)[:500]
                ai_session.commit()
            logger.warning("Đồng bộ embedding thất bại", exc_info=True)
            return run
