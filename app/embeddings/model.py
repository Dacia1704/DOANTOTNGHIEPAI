"""Nạp model embedding (sentence-transformers), chạy local trên CPU."""

from __future__ import annotations

import logging
import threading

from app.config import get_settings

logger = logging.getLogger("ai.embeddings")

_lock = threading.Lock()
_model = None


class EmbeddingModel:
    """Bọc model sentence-transformers với tiền tố query:/passage: của E5."""

    def __init__(self, name: str, dim: int) -> None:
        self.name = name
        self.dim = dim
        self._model = None

    def load(self) -> None:
        if self._model is not None:
            return
        from sentence_transformers import SentenceTransformer

        logger.info("Đang nạp model embedding %s", self.name)
        self._model = SentenceTransformer(self.name, device="cpu")
        actual = self._model.get_sentence_embedding_dimension()
        if actual and actual != self.dim:
            logger.warning(
                "Model %s có %d chiều nhưng cấu hình là %d. Sẽ dùng giá trị của model.",
                self.name,
                actual,
                self.dim,
            )
            self.dim = actual
        logger.info("Đã nạp model embedding %s (%d chiều)", self.name, self.dim)

    def encode(self, texts: list[str], *, is_query: bool = False) -> "list[list[float]]":
        self.load()
        prefix = "query: " if is_query else "passage: "
        vectors = self._model.encode(
            [prefix + text for text in texts],
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [vector.tolist() for vector in vectors]


_embedding_model: EmbeddingModel | None = None


def get_embedding_model() -> EmbeddingModel:
    global _embedding_model
    if _embedding_model is None:
        with _lock:
            if _embedding_model is None:
                settings = get_settings()
                _embedding_model = EmbeddingModel(
                    settings.embedding_model, settings.embedding_dim
                )
    return _embedding_model


def load_embedding_model() -> None:
    """Gọi lúc khởi động app để nạp model sẵn."""
    try:
        get_embedding_model().load()
    except Exception:
        logger.warning("Không nạp được model embedding", exc_info=True)
