"""MMR (Maximal Marginal Relevance) để đa dạng hóa kết quả."""

from __future__ import annotations

import numpy as np


def mmr_rerank(
    candidates: list,
    scores: list[float],
    vectors: dict[str, np.ndarray],
    *,
    top_k: int = 10,
    lambda_relevance: float = 0.7,
) -> list:
    """Chọn top_k theo MMR: ưu tiên liên quan nhưng tránh tin trùng nhau.

    `candidates` là list object có thuộc tính `listing_id`.
    `scores` là điểm liên quan tương ứng (càng lớn càng tốt).
    """
    if not candidates:
        return []

    order = sorted(range(len(candidates)), key=lambda i: scores[i], reverse=True)
    pool = order[: min(len(order), max(top_k * 5, 50))]
    selected: list[int] = []

    while pool and len(selected) < top_k:
        best_index = None
        best_value = -float("inf")
        for index in pool:
            relevance = scores[index]
            redundancy = 0.0
            candidate_vector = vectors.get(candidates[index].listing_id)
            if candidate_vector is not None:
                for chosen in selected:
                    chosen_vector = vectors.get(candidates[chosen].listing_id)
                    if chosen_vector is None:
                        continue
                    similarity = float(np.dot(candidate_vector, chosen_vector))
                    redundancy = max(redundancy, similarity)
            value = lambda_relevance * relevance - (1 - lambda_relevance) * redundancy
            if value > best_value:
                best_value = value
                best_index = index
        if best_index is None:
            break
        selected.append(best_index)
        pool.remove(best_index)

    return [candidates[i] for i in selected]
