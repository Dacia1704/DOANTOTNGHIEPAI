"""Offline holdout on the current catalogue; never use the held-out listing in the user vector.

This is not historical replay: profiles/catalogue are current snapshots.
Usage: python -m scripts.evaluate_recsys --top-k 10
"""

import argparse
import json
import math
import uuid

from sqlalchemy import select, union

from app.config import get_settings
from app.db.models_ai import AiEvalRun
from app.db.models_core import ListingContactEvent, SavedListing
from app.db.session import ai_session_factory, core_session_factory
from app.embeddings.store import embedding_store
from app.recommend.candidates import fetch_candidates
from app.recommend.scoring import DEFAULT_WEIGHTS, ProfileSnapshot, hard_filter_pass
from app.recommend.service import load_profile, rank_candidates
from app.recommend.user_vector import build_user_vector, load_interactions


def split_holdout(interactions):
    positives = sorted(
        (i for i in interactions if i.kind in ("SAVE", "CONTACT")),
        key=lambda i: i.created_at,
    )
    if not positives:
        return None, []
    held = positives[-1]
    train = [
        i
        for i in interactions
        if i.created_at < held.created_at and i.listing_id != held.listing_id
    ]
    return held, train


def evaluate(top_k=10, min_relevant=1):
    if top_k < 1:
        raise ValueError("top_k must be positive")
    embedding_store.load(get_settings().embedding_model)
    outcomes = {name: [] for name in ("profile_only", "profile_behavior", "full")}
    with core_session_factory() as session:
        users = session.scalars(
            union(select(SavedListing.tenant_id), select(ListingContactEvent.tenant_id))
        ).all()
        for uid in users:
            interactions = load_interactions(session, uid)
            if (
                len(
                    {
                        i.listing_id
                        for i in interactions
                        if i.kind in ("SAVE", "CONTACT")
                    }
                )
                < min_relevant
            ):
                continue
            held, train = split_holdout(interactions)
            if held is None:
                continue
            profile = load_profile(session, uid) or ProfileSnapshot()
            if not profile.allow_personalization:
                profile = ProfileSnapshot(allow_personalization=False)
                train = []
            candidates = fetch_candidates(
                session,
                province_id=profile.desired_province_id,
                ward_ids=[
                    w for w in (profile.desired_ward_id, *profile.extra_ward_ids) if w
                ],
                price_min=profile.price_min,
                price_max=profile.price_max,
                tolerance_pct=profile.price_tolerance_pct,
                required_amenity_ids=profile.required_amenity_ids,
            )
            candidates = [
                c
                for c in candidates
                if hard_filter_pass(c, profile) and c.published_at <= held.created_at
            ]
            vector, _ = build_user_vector(train, embedding_store, held.created_at)
            for name in outcomes:
                weights = dict(DEFAULT_WEIGHTS)
                if name == "profile_only":
                    weights = {
                        "profile": 1.0,
                        "behavior": 0.0,
                        "freshness": 0.0,
                        "boost": 0.0,
                    }
                elif name == "profile_behavior":
                    weights = {
                        "profile": 0.50 / 0.85,
                        "behavior": 0.35 / 0.85,
                        "freshness": 0.0,
                        "boost": 0.0,
                    }
                if vector is None:
                    weights["profile"] += weights["behavior"]
                    weights["behavior"] = 0.0
                ranked = rank_candidates(
                    candidates, profile, vector, held.created_at, top_k, weights
                )
                ids = [item.listing_id for item in ranked]
                rank = ids.index(held.listing_id) if held.listing_id in ids else None
                outcomes[name].append(
                    (
                        float(rank is not None),
                        0.0 if rank is None else 1 / math.log2(rank + 2),
                    )
                )
    metrics = {
        name: {
            f"recall@{top_k}": sum(r for r, n in rows) / len(rows) if rows else 0.0,
            f"ndcg@{top_k}": sum(n for r, n in rows) / len(rows) if rows else 0.0,
            "n_users": len(rows),
        }
        for name, rows in outcomes.items()
    }
    return {
        "per_config": metrics,
        "dataset_note": "Latest positive held out; all its interactions excluded from training. Current profiles/catalogue, not historical replay. Synthetic data does not establish real-world quality.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args()
    outcome = evaluate(args.top_k)
    with ai_session_factory() as session:
        for name, metrics in outcome["per_config"].items():
            session.add(
                AiEvalRun(
                    id=str(uuid.uuid4()),
                    kind="RECSYS",
                    name=name,
                    config={"top_k": args.top_k},
                    metrics=metrics,
                    dataset_note=outcome["dataset_note"][:255],
                )
            )
        session.commit()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
