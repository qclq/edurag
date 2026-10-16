from __future__ import annotations

import json
from collections.abc import Iterator

import httpx

from app.core.config import Settings


class LlmProviderError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


SYSTEM_PROMPT = """你是智慧校园知识库问答助手。
只依据提供的“校园资料”回答，不得使用无引用的常识补充学校政策结论。
每项事实结论必须用 [1]、[2] 形式标注资料编号；没有可靠依据时明确回答“当前知识库中没有找到足够可靠的依据”。
若资料互相冲突，指出冲突并建议向文件发布部门核实。回答使用简洁、清晰的中文。"""

QUERY_PLANNER_PROMPT = """你是知识库检索规划器。请把用户问题拆分为 1 到 4 条可独立执行的中文检索查询。
要求：
1. 保留文件名、制度名、年份、部门、身份和专业术语等关键限定；
2. 删除“我想知道、请问、我应该、能否、怎样”等对检索无帮助的口语包装，至少生成一条关键词式查询；
3. 补全口语中的指代，但不要添加用户没有表达的事实；
4. 每条查询聚焦一个检索意图，适合直接用于向量检索；
5. 只返回 JSON，格式必须是 {"queries":["查询1","查询2"]}。
示例：用户问“我应该使用怎样的框架？”时，可返回 {"queries":["使用框架","框架"]}。"""


def _extractive_answer(chunks: list[dict[str, object]]) -> str:
    if not chunks:
        return "当前知识库中没有找到足够可靠的依据，建议补充问题信息或联系相关部门。"
    lines = ["根据当前知识库中的资料："]
    for index, chunk in enumerate(chunks[:3], 1):
        content = str(chunk.get("content", "")).strip().replace("\n", " ")
        if len(content) > 240:
            content = content[:237] + "…"
        lines.append(f"{index}. {content} [{index}]")
    lines.append("请以引用的正式文件为准；如文件已有更新，请向对应发布部门核实。")
    return "\n\n".join(lines)


def _request_body(question: str, chunks: list[dict[str, object]], settings: Settings, *, stream: bool) -> dict[str, object]:
    context = "\n\n".join(
        f"[{index}] 文件：{chunk.get('documentName', '')}\n章节：{chunk.get('section') or '未标注'}\n{chunk.get('content', '')}"
        for index, chunk in enumerate(chunks, 1)
    )
    body: dict[str, object] = {
        "model": settings.deepseek_model,
        "temperature": settings.llm_temperature,
        "max_tokens": settings.llm_max_tokens,
        "stream": stream,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"用户问题：{question}\n\n校园资料：\n{context or '（无检索结果）'}"},
        ],
        "thinking": {"type": settings.deepseek_thinking},
    }
    if settings.deepseek_thinking == "enabled":
        body["reasoning_effort"] = settings.deepseek_reasoning_effort
    return body


def _ensure_deepseek(settings: Settings) -> None:
    if not settings.deepseek_api_key:
        raise LlmProviderError("LLM_NOT_CONFIGURED", "DeepSeek 尚未配置，请设置 DEEPSEEK_API_KEY")


def plan_retrieval_queries(question: str, settings: Settings, *, max_queries: int = 4) -> list[str]:
    """Use the answer model to turn a user question into focused retrieval queries.

    The original question is always retained so a planner cannot accidentally
    remove exact names or other important wording from the retrieval request.
    """
    question = question.strip()
    if not question:
        return []
    if settings.llm_provider == "extractive":
        return [question]
    _ensure_deepseek(settings)
    body: dict[str, object] = {
        "model": settings.deepseek_model,
        "temperature": 0,
        "max_tokens": 500,
        "stream": False,
        "messages": [
            {"role": "system", "content": QUERY_PLANNER_PROMPT},
            {"role": "user", "content": question},
        ],
        "thinking": {"type": "disabled"},
    }
    try:
        response = httpx.post(
            f"{settings.deepseek_base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.deepseek_api_key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=settings.llm_timeout_seconds,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise TypeError("planner content is not text")
        stripped = content.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            start, end = stripped.find("{"), stripped.rfind("}")
            if start < 0 or end <= start:
                raise
            payload = json.loads(stripped[start : end + 1])
        raw_queries = payload.get("queries", [])
        if not isinstance(raw_queries, list):
            raise TypeError("planner queries is not a list")
    except (httpx.HTTPError, json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as exc:
        raise LlmProviderError("QUERY_PLANNING_FAILED", "DeepSeek 检索问题拆分失败") from exc

    planned: list[str] = []
    for item in raw_queries:
        value = str(item).strip()
        if value and value not in planned and value != question:
            planned.append(value)
    return [*planned[: max(0, max_queries - 1)], question]


def generate_answer(question: str, chunks: list[dict[str, object]], settings: Settings) -> tuple[str, str]:
    if settings.llm_provider == "extractive":
        return _extractive_answer(chunks), "extractive"
    _ensure_deepseek(settings)
    try:
        response = httpx.post(
            f"{settings.deepseek_base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.deepseek_api_key}",
                "Content-Type": "application/json",
            },
            json=_request_body(question, chunks, settings, stream=False),
            timeout=settings.llm_timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        raise LlmProviderError("LLM_PROVIDER_ERROR", "DeepSeek 问答服务调用失败") from exc
    if not isinstance(content, str) or not content.strip():
        raise LlmProviderError("LLM_RESPONSE_INVALID", "DeepSeek 返回了空回答")
    return content.strip(), settings.deepseek_model


def stream_answer(question: str, chunks: list[dict[str, object]], settings: Settings) -> Iterator[str]:
    if settings.llm_provider == "extractive":
        answer = _extractive_answer(chunks)
        for offset in range(0, len(answer), 80):
            yield answer[offset : offset + 80]
        return
    _ensure_deepseek(settings)
    try:
        with httpx.stream(
            "POST",
            f"{settings.deepseek_base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.deepseek_api_key}",
                "Content-Type": "application/json",
            },
            json=_request_body(question, chunks, settings, stream=True),
            timeout=settings.llm_timeout_seconds,
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                value = line[5:].strip()
                if value == "[DONE]":
                    break
                payload = json.loads(value)
                delta = payload.get("choices", [{}])[0].get("delta", {}).get("content")
                if delta:
                    yield str(delta)
    except (httpx.HTTPError, json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as exc:
        raise LlmProviderError("LLM_PROVIDER_ERROR", "DeepSeek 流式问答调用失败") from exc
