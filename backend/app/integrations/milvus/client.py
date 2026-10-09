from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.config import Settings


class MilvusIndex:
    """Small pymilvus adapter; import is lazy so API-only tests stay isolated."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        from pymilvus import MilvusClient

        self.client = MilvusClient(
            uri=settings.milvus_uri,
            token=settings.milvus_token or "",
            db_name=settings.milvus_database,
            timeout=settings.milvus_timeout_seconds,
        )

    def ensure_collection(self, collection_name: str) -> None:
        if not self.client.has_collection(collection_name=collection_name):
            from pymilvus import DataType

            schema = self.client.create_schema(auto_id=False, enable_dynamic_field=False)
            schema.add_field("chunk_id", DataType.VARCHAR, is_primary=True, max_length=64)
            schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=self.settings.embedding_dimension)
            for name in ("knowledge_base_id", "document_id", "document_version_id", "embedding_model"):
                schema.add_field(name, DataType.VARCHAR, max_length=128)
            schema.add_field("is_active", DataType.BOOL)
            schema.add_field("expires_at", DataType.INT64)
            schema.add_field(
                "search_acl_tokens",
                DataType.ARRAY,
                element_type=DataType.VARCHAR,
                max_capacity=128,
                max_length=128,
            )
            index = self.client.prepare_index_params()
            index.add_index(
                "embedding",
                index_type="HNSW",
                metric_type="COSINE",
                params={"M": 16, "efConstruction": 200},
            )
            self.client.create_collection(collection_name=collection_name, schema=schema, index_params=index)
        alias = self.settings.milvus_collection_alias
        try:
            self.client.create_alias(collection_name=collection_name, alias=alias)
        except Exception:
            # An existing alias is intentionally not moved here. Model changes
            # must finish a full rebuild and validation before an explicit switch.
            pass

    def switch_alias(self, collection_name: str) -> None:
        """Atomically switch the stable query alias after a validated full build."""
        self.ensure_collection(collection_name)
        self.client.alter_alias(collection_name=collection_name, alias=self.settings.milvus_collection_alias)

    def upsert(self, collection_name: str, rows: list[dict[str, Any]]) -> None:
        self.ensure_collection(collection_name)
        self.client.upsert(collection_name=collection_name, data=rows)

    def delete(self, collection_name: str, chunk_ids: list[str]) -> None:
        if chunk_ids and self.client.has_collection(collection_name=collection_name):
            quoted = ",".join(f'"{item}"' for item in chunk_ids)
            self.client.delete(collection_name=collection_name, filter=f"chunk_id in [{quoted}]")

    def search(self, vector: list[float], kb_ids: list[str], acl_tokens: list[str], limit: int) -> list[dict[str, Any]]:
        alias = self.settings.milvus_collection_alias
        kb_values = ",".join(f'"{item}"' for item in kb_ids)
        expressions = ["is_active == true", f"knowledge_base_id in [{kb_values}]"]
        now_epoch = int(datetime.now(UTC).timestamp())
        expressions.append(f"(expires_at == 0 or expires_at >= {now_epoch})")
        if acl_tokens:
            acl_checks = " or ".join(f'array_contains(search_acl_tokens, "{token}")' for token in acl_tokens)
            expressions.append(f"({acl_checks})")
        result = self.client.search(
            collection_name=alias,
            data=[vector],
            filter=" and ".join(expressions),
            limit=limit,
            output_fields=["chunk_id", "document_id", "knowledge_base_id"],
            search_params={"metric_type": "COSINE", "params": {"ef": max(64, limit * 4)}},
        )
        return [
            {"chunkId": hit["entity"]["chunk_id"], "score": float(hit["distance"]), **hit["entity"]}
            for hit in (result[0] if result else [])
        ]
