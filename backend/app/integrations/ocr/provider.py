from __future__ import annotations

import base64

import httpx

from app.core.config import Settings


class OcrProviderError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def ocr_image(content: bytes, mime_type: str, settings: Settings) -> str:
    """Extract image text with Qwen OCR through its OpenAI-compatible API."""
    if not settings.qwen_api_key:
        raise OcrProviderError("OCR_NOT_CONFIGURED", "Qwen OCR 尚未配置，请设置 QWEN_API_KEY")
    encoded = base64.b64encode(content).decode("ascii")
    data_url = f"data:{mime_type};base64,{encoded}"
    try:
        response = httpx.post(
            f"{settings.qwen_ocr_base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.qwen_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.qwen_ocr_model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": data_url},
                                "min_pixels": 3072,
                                "max_pixels": 8388608,
                            },
                            {
                                "type": "text",
                                "text": (
                                    "请完整识别图片中的可见文字、表格和公式。保持原有阅读顺序；"
                                    "表格使用 Markdown 表格表达；只输出识别结果，不要解释，也不要臆造。"
                                ),
                            },
                        ],
                    }
                ],
            },
            timeout=settings.qwen_ocr_timeout_seconds,
        )
        response.raise_for_status()
        result = response.json()["choices"][0]["message"]["content"]
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status in {401, 403}:
            raise OcrProviderError("OCR_AUTH_ERROR", f"Qwen OCR 鉴权失败（HTTP {status}），请检查 API Key 与模型地域") from exc
        if status == 404:
            raise OcrProviderError("OCR_PROVIDER_NOT_FOUND", "Qwen OCR 模型或接口不可用（HTTP 404），请检查模型名称与接口地址") from exc
        if status == 429:
            raise OcrProviderError("OCR_RATE_LIMITED", "Qwen OCR 请求受限（HTTP 429），请检查账户额度或稍后重试") from exc
        raise OcrProviderError("OCR_PROVIDER_ERROR", f"Qwen OCR 服务调用失败（HTTP {status}）") from exc
    except httpx.TimeoutException as exc:
        raise OcrProviderError("OCR_TIMEOUT", "Qwen OCR 请求超时，请稍后重试") from exc
    except httpx.HTTPError as exc:
        raise OcrProviderError("OCR_CONNECTION_ERROR", "无法连接 Qwen OCR 服务，请检查网络与接口地址") from exc
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise OcrProviderError("OCR_RESPONSE_INVALID", "Qwen OCR 返回格式不正确") from exc
    if not isinstance(result, str):
        raise OcrProviderError("OCR_RESPONSE_INVALID", "Qwen OCR 返回格式不正确")
    return result.strip()
