from __future__ import annotations

import math
import re
import time
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.config import Settings
from app.integrations.embedding import embed_texts
from app.modules.documents.models import Document, DocumentChunk, DocumentVersion
from app.modules.documents.service import document_acl_allows
from app.modules.knowledge_bases.models import KnowledgeBase
from app.modules.knowledge_bases.service import list_accessible
from app.modules.users.models import User


def tokenize(text: str) -> set[str]:
    lowered = text.lower()
    tokens = set(re.findall(r"[a-z0-9]+", lowered))
    cjk = "".join(re.findall(r"[\u4e00-\u9fff]", lowered))
    tokens.update(cjk[index : index + 2] for index in range(max(0, len(cjk) - 1)))
    tokens.update(cjk)
    return {token for token in tokens if token}


def acl_tokens(user: User) -> list[str]:
    values = ["public", f"user:{user.id}"]
    if user.department_id:
        values.append(f"department:{user.department_id}")
    values.extend(f"role:{role.id}" for role in user.roles)
    return values


def retrieve(
    db: Session,
    user: User,
    query: str,
    settings: Settings,
    *,
    knowledge_base_ids: list | None = None,
    document_id=None,
    top_k: int = 5,
    min_score: float = 0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    started = time.perf_counter()
    accessible = list_accessible(db, user, "search")
    allowed_ids = {kb.id for kb in accessible}
    if knowledge_base_ids is not None:
        allowed_ids.intersection_update(knowledge_base_ids)
    if not allowed_ids:
        return [], {"retrievalMs": 0, "denseAvailable": False, "candidateCount": 0}
    statement = (
        select(DocumentChunk, Document, DocumentVersion, KnowledgeBase)
        .join(DocumentVersion, DocumentVersion.id == DocumentChunk.document_version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .join(KnowledgeBase, KnowledgeBase.id == Document.knowledge_base_id)
        .where(
            Document.knowledge_base_id.in_(allowed_ids),
            Document.deleted_at.is_(None),
            Document.enabled.is_(True),
            Document.status == "ready",
            DocumentVersion.is_current.is_(True),
            DocumentVersion.process_status == "ready",
            KnowledgeBase.status == "enabled",
        )
    )
    if document_id:
        statement = statement.where(Document.id == document_id)
    rows = db.execute(statement).all()
    today = date.today()
    query_tokens = tokenize(query)
    sparse: dict[str, float] = {}
    by_id: dict[str, tuple] = {}
    for chunk, document, version, kb in rows:
        if document.expires_at and document.expires_at < today:
            continue
        if not document_acl_allows(db, document, user, "search"):
            continue
        tokens = tokenize(chunk.search_tokens or chunk.content)
        overlap = len(query_tokens.intersection(tokens))
        phrase_bonus = 1.0 if query.lower() in chunk.content.lower() else 0.0
        score = min(1.0, (overlap / max(1, math.sqrt(len(query_tokens) * max(1, len(tokens))))) + phrase_bonus)
        sparse[str(chunk.id)] = score
        by_id[str(chunk.id)] = (chunk, document, version, kb)

    dense: dict[str, float] = {}
    dense_available = False
    if settings.milvus_enabled and rows:
        try:
            from app.integrations.milvus import MilvusIndex

            vector = embed_texts([query], settings)[0]
            hits = MilvusIndex(settings).search(
                vector,
                [str(item) for item in allowed_ids],
                acl_tokens(user),
                max(top_k * 4, 20),
            )
            dense = {str(hit["chunkId"]): float(hit["score"]) for hit in hits}
            dense_available = True
        except Exception:
            # PostgreSQL remains the source of truth; a Milvus outage degrades
            # retrieval but never bypasses authorization or returns stale text.
            dense_available = False

    ranked: list[tuple[float, str]] = []
    for chunk_id in set(sparse).union(dense):
        if chunk_id not in by_id:
            continue
        sparse_score = sparse.get(chunk_id, 0.0)
        dense_score = dense.get(chunk_id, 0.0)
        fused = 0.45 * sparse_score + (0.55 * dense_score if dense_available else 0.55 * sparse_score)
        # Knowledge-base thresholds historically represented the similarity of
        # the strongest retrieval channel. Applying them only to the weighted
        # fusion score discarded good dense hits (for example 0.70 dense became
        # 0.385 after weighting). Keep fusion for ranking, but qualify a result
        # with the strongest available retrieval signal.
        qualification_score = max(fused, dense_score if dense_available else sparse_score)
        if qualification_score >= min_score:
            ranked.append((fused, chunk_id))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    results: list[dict[str, Any]] = []
    for score, chunk_id in ranked[:top_k]:
        chunk, document, version, kb = by_id[chunk_id]
        results.append(
            {
                "chunkId": str(chunk.id),
                "documentId": str(document.id),
                "documentVersionId": str(version.id),
                "knowledgeBaseId": str(kb.id),
                "knowledgeBaseName": kb.name,
                "documentName": document.display_name,
                "section": chunk.section,
                "pageStart": chunk.page_start,
                "pageEnd": chunk.page_end,
                "content": chunk.content,
                "score": round(score, 6),
                "sparseScore": round(sparse.get(chunk_id, 0), 6),
                "denseScore": round(dense.get(chunk_id, 0), 6) if dense_available else None,
            }
        )
    elapsed = int((time.perf_counter() - started) * 1000)
    return results, {
        "retrievalMs": elapsed,
        "denseAvailable": dense_available,
        "candidateCount": len(rows),
        "returnedCount": len(results),
    }


def retrieve_queries(
    db: Session,
    user: User,
    queries: list[str],
    settings: Settings,
    *,
    knowledge_base_ids: list | None = None,
    top_k: int = 8,
    min_score: float = 0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Retrieve multiple planned queries and merge duplicate chunks.

    Authorization remains enforced inside ``retrieve`` for every query. A chunk
    matching several decomposed queries receives a small ranking boost while
    its original similarity score is preserved for citation diagnostics.
    """
    started = time.perf_counter()
    unique_queries = list(dict.fromkeys(query.strip() for query in queries if query.strip()))
    merged: dict[str, dict[str, Any]] = {}
    summaries: list[dict[str, Any]] = []
    for query in unique_queries:
        results, summary = retrieve(
            db,
            user,
            query,
            settings,
            knowledge_base_ids=knowledge_base_ids,
            top_k=top_k,
            min_score=min_score,
        )
        summaries.append(summary)
        for item in results:
            chunk_id = str(item["chunkId"])
            existing = merged.get(chunk_id)
            if existing is None:
                merged[chunk_id] = {**item, "matchedQueries": [query]}
                continue
            matched = existing.setdefault("matchedQueries", [])
            if query not in matched:
                matched.append(query)
            if float(item["score"]) > float(existing["score"]):
                merged[chunk_id] = {**item, "matchedQueries": matched}

    ranked = sorted(
        merged.values(),
        key=lambda item: (
            -(float(item["score"]) + min(0.08, 0.02 * (len(item.get("matchedQueries", [])) - 1))),
            str(item["chunkId"]),
        ),
    )[:top_k]
    return ranked, {
        "retrievalMs": int((time.perf_counter() - started) * 1000),
        "denseAvailable": any(item.get("denseAvailable") for item in summaries),
        "candidateCount": max((int(item.get("candidateCount", 0)) for item in summaries), default=0),
        "returnedCount": len(ranked),
        "queryCount": len(unique_queries),
        "queries": unique_queries,
    }
