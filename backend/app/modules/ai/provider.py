"""模型适配：OpenAI 兼容的 `/chat/completions`（设计文档 §23）。

- 只用 `httpx` 直连 `settings.AI_BASE_URL`，**不引入任何厂商 SDK**。
- 请求带超时、输入长度上限、重试上限；预算前置检查由调用方 `service.py` 在调用前完成
  （预算事实在数据库，适配层不访问数据库）。
- **不编造模型能力或价格**：token 与成本只按响应中的 `usage` 字段记录；缺失时记 0，
  并由调用方在结果中标注"成本未知"。
- P1 不做多厂商网关、模型路由与熔断框架（§23、§34）。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx

from app.config import settings

logger = logging.getLogger("app.ai")

# 输入规模上限：超过即拒绝，避免超长提示导致不可控成本（§23）。
MAX_PROMPT_CHARS = 20_000
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_MAX_RETRIES = 2

# 需要重试的瞬时状态码；4xx（除 429）为永久错误，不重试（§22.2）。
_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class ProviderError(Exception):
    """永久性失败：配置缺失、请求非法、模型返回不可用结果。不重试。"""


class ProviderUnavailable(Exception):
    """瞬时失败：超时、连接错误或可重试状态码重试后仍失败。可重试。"""


@dataclass(frozen=True)
class ChatResult:
    """模型响应。token/成本按 usage 记录，缺失时成本未知。"""

    content: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_cents: int
    cost_known: bool


def _endpoint() -> str:
    base = (settings.AI_BASE_URL or "").strip().rstrip("/")
    if not base:
        raise ProviderError("未配置 AI_BASE_URL")
    return f"{base}/chat/completions"


def _prompt_chars(messages: list[dict[str, str]]) -> int:
    return sum(len(message.get("content") or "") for message in messages)


def _parse_cost(usage: dict, payload: dict) -> tuple[int, bool]:
    """从响应中读取成本。**不推算价格**，缺失即视为成本未知（§23）。"""
    for source in (usage, payload):
        for key in ("cost_cents", "cost", "total_cost_cents"):
            value = source.get(key)
            if isinstance(value, (int, float)) and value >= 0:
                return int(round(value)), True
    return 0, False


def _parse_response(payload: dict, *, requested_model: str) -> ChatResult:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ProviderError("模型响应缺少 choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = ""
    if isinstance(message, dict):
        raw = message.get("content")
        if isinstance(raw, str):
            content = raw
    if not content.strip():
        raise ProviderError("模型返回空内容")

    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    input_tokens = usage.get("prompt_tokens")
    output_tokens = usage.get("completion_tokens")
    cost_cents, cost_known = _parse_cost(usage, payload)

    model = payload.get("model")
    if not isinstance(model, str) or not model:
        model = requested_model
    return ChatResult(
        content=content,
        model=model,
        input_tokens=int(input_tokens) if isinstance(input_tokens, (int, float)) else 0,
        output_tokens=int(output_tokens) if isinstance(output_tokens, (int, float)) else 0,
        cost_cents=cost_cents,
        cost_known=cost_known,
    )


async def chat(
    messages: list[dict[str, str]],
    *,
    max_output_tokens: int,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> ChatResult:
    """调用 OpenAI 兼容的 `/chat/completions`。

    永久错误抛 `ProviderError`；重试耗尽抛 `ProviderUnavailable`。
    调用方必须先完成预算前置检查与内容权限判定。
    """
    if not settings.AI_ENABLED:
        # 适配层不产生"假结果"：未启用时显式失败（§25.2）。
        raise ProviderError("AI 功能未启用")
    if not settings.AI_MODEL:
        raise ProviderError("未配置 AI_MODEL")
    if _prompt_chars(messages) > MAX_PROMPT_CHARS:
        raise ProviderError(f"输入超过上限 {MAX_PROMPT_CHARS} 字符")

    url = _endpoint()
    headers = {"Content-Type": "application/json"}
    if settings.AI_API_KEY:
        headers["Authorization"] = f"Bearer {settings.AI_API_KEY}"
    body = {
        "model": settings.AI_MODEL,
        "messages": messages,
        "max_tokens": max_output_tokens,
        "stream": False,
    }

    last_error: Exception | None = None
    attempts = max(0, max_retries) + 1
    for attempt in range(attempts):
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.post(url, headers=headers, json=body)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = exc
            logger.warning("ai_provider_transport_error", extra={"error": type(exc).__name__})
        else:
            if response.status_code == 200:
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise ProviderError("模型响应不是合法 JSON") from exc
                if not isinstance(payload, dict):
                    raise ProviderError("模型响应格式异常")
                return _parse_response(payload, requested_model=settings.AI_MODEL)
            if response.status_code not in _RETRYABLE_STATUS:
                raise ProviderError(f"模型请求被拒绝（状态码 {response.status_code}）")
            last_error = ProviderUnavailable(f"模型暂时不可用（状态码 {response.status_code}）")
            logger.warning("ai_provider_retryable", extra={"status": response.status_code})

        if attempt < attempts - 1:
            await asyncio.sleep(min(2**attempt, 4))

    raise ProviderUnavailable("模型暂时不可用，请稍后重试") from last_error
