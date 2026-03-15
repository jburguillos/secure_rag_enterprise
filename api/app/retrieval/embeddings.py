"""Embedding services."""

from __future__ import annotations

import hashlib

from llama_index.embeddings.ollama import OllamaEmbedding

from app.config import get_settings


def _hash_embedding(text: str, dim: int = 768) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    values: list[float] = []
    seed = digest
    while len(values) < dim:
        seed = hashlib.sha256(seed).digest()
        for byte in seed:
            values.append((byte / 255.0) * 2.0 - 1.0)
            if len(values) >= dim:
                break
    return values


class EmbeddingService:
    """Generate embeddings using configured local provider."""

    def __init__(self) -> None:
        settings = get_settings()
        self.settings = settings
        self.batch_size = max(1, settings.embedding_batch_size)
        self._ollama = OllamaEmbedding(
            model_name=settings.ollama_embed_model,
            base_url=settings.ollama_base_url,
        )

    def embed_text(self, text: str) -> list[float]:
        try:
            return list(self._ollama.get_text_embedding(text))
        except Exception:
            return _hash_embedding(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        vectors: list[list[float]] = []
        for idx in range(0, len(texts), self.batch_size):
            batch = texts[idx : idx + self.batch_size]
            try:
                batch_vectors = [list(v) for v in self._ollama.get_text_embedding_batch(batch)]
                if len(batch_vectors) != len(batch):
                    raise ValueError("embedding batch size mismatch")
                vectors.extend(batch_vectors)
            except Exception:
                vectors.extend([self.embed_text(text) for text in batch])
        return vectors
