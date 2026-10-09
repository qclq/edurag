from __future__ import annotations

import hashlib
import math
import re

import httpx

from app.core.config import Settings


class EmbeddingProviderError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _tokens(text: str) -> list[str]:
    lowered = text.lower()
    words = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", lowered)
    cjk = "".join(part for part in words if len(part) == 1 and "\u4e00" <= part <= "\u9fff")
    words.extend(cjk[index : index + 2] for index in range(max(0, len(cjk) - 1)))
    return words


def _local_embeddings(texts: list[str], dimension: int) -> list[list[float]]:
    vectors: list[list[float]] = []
    for text in texts:
        vector = [0.0] * dimension
        for token in _tokens(text):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=16).digest()
            index = int.from_bytes(digest[:8], "big") % dimension
            vector[index] += -1.0 if digest[8] & 1 else 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        vectors.append([value / norm for value in vector])
    return vectors


def _qwen_batch(texts: list[str], settings: Settings) -> list[list[float]]:
    if not settings.qwen_api_key:
        raise EmbeddingProviderError(
            "EMBEDDING_NOT_CONFIGURED",
            "Qwen Embedding 尚未配置，请设置 QWEN_API_KEY",
        )
    try:
        response = httpx.post(
            f"{settings.qwen_embedding_base_url.rstrip('/')}/embeddings",
            headers={
                "Authorization": f"Bearer {settings.qwen_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.embedding_model,
                "input": texts,
                "dimensions": settings.embedding_dimension,
                "encoding_format": "float",
            },
            timeout=settings.embedding_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise EmbeddingProviderError("EMBEDDING_PROVIDER_ERROR", "Qwen Embedding 调用失败") from exc
    try:
        ordered = sorted(payload["data"], key=lambda item: item["index"])
        vectors = [item["embedding"] for item in ordered]
    except (KeyError, TypeError) as exc:
        raise EmbeddingProviderError("EMBEDDING_RESPONSE_INVALID", "Qwen Embedding 返回格式不正确") from exc
    if len(vectors) != len(texts) or any(len(vector) != settings.embedding_dimension for vector in vectors):
        raise EmbeddingProviderError("EMBEDDING_DIMENSION_MISMATCH", "Qwen Embedding 返回向量数量或维度不符合配置")
    return vectors


def embed_texts(texts: list[str], settings: Settings) -> list[list[float]]:
    if not texts:
        return []
    if settings.embedding_provider == "local":
        return _local_embeddings(texts, settings.embedding_dimension)
    vectors: list[list[float]] = []
    batch_size = min(settings.embedding_batch_size, 10)
    for offset in range(0, len(texts), batch_size):
        vectors.extend(_qwen_batch(texts[offset : offset + batch_size], settings))
    return vectors
