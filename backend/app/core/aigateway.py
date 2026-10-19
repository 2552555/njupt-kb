"""AI Gateway 安全构建块（B 块）。

纯 Python、可单测、不擅自接入线上 AI 供应商。对应：
- 规划设计/AI对抗攻击防御设计.md：P0 防自动化基线、提示注入防护、AI Gateway 熔断/预算 §6
- 规划设计/rongyu.md：AI 是增强非依赖、熔断/异步/幂等/故障隔离

设计约束（来自 app/config.py 已接入项）：
    AI_GATEWAY_TIMEOUT_SECONDS     # 单次调用超时
    AI_GATEWAY_FAILURE_THRESHOLD   # 连续失败阈值（熔断）
    AI_GATEWAY_COOLDOWN_SECONDS    # 熔断后冷却
    AI_REQUEST_MAX_TOKENS          # 单次请求累计 token 预算
    AI_REQUEST_MAX_CALLS           # 单次请求调用次数预算

NOT_RUN 项（本机无 Redis / 真实供应商，不伪造已完成）：
- 线上 AI 供应商接入：call_ai_safe 启用分支仅抛 NotImplementedError 占位，由 breaker 捕获降级。
- 熔断/预算的跨进程共享：当前为进程内 dict 单例，生产应改用 Redis（见各组件注释）。
"""

from __future__ import annotations

import asyncio
import re
import time

from app.config import settings


# --- 提示注入防护：基础指令越权标记（大小写/多语言变体） --------------------
# 仅做“可解释、可单测”的基础剥离；生产应在网关层叠加更严格的策略与日志告警。
_INJECTION_PATTERNS: tuple[str, ...] = (
    r"ignore\s+previous\s+instructions",
    r"ignore\s+prior\s+instructions",
    r"disregard\s+(?:the\s+)?previous\s+instructions",
    r"disregard\s+the\s+above",
    r"forget\s+(?:your|all)\s+instructions",
    # 中文变体：忽略/忘记 与 指令 之间允许少量字符（如“之前的”）。
    r"忽略[\s\S]{0,8}?指令",
    r"忘记[\s\S]{0,8}?指令",
)
_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)
# 允许的空白控制字符（保留换行/回车/制表），其余控制字符视为不可信并去除。
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_ai_prompt(user_input: str) -> str:
    """对不可信用户输入做提示注入的基础处理。

    1. 去除控制字符，避免注入隐藏指令/分隔符越权；
    2. 标记（剥离）常见“忽略/忘记指令”类越权短语；
    3. 用清晰边界分隔符包裹，提示模型区分系统指令与用户内容。

    保持简单可解释：越权短语被替换为固定标记，正常文本原样保留。
    """
    if not isinstance(user_input, str):
        user_input = str(user_input)
    # 1. 去除控制字符
    cleaned = _CONTROL_CHARS_RE.sub("", user_input)
    # 2. 标记疑似提示注入短语
    cleaned = _INJECTION_RE.sub("[已拦截:疑似提示注入]", cleaned)
    # 3. 明确可信边界，避免用户内容被误读为系统指令
    return "[用户内容开始]\n" + cleaned.strip() + "\n[用户内容结束]"


class CircuitBreaker:
    """基于内存状态的熔断器（生产应改用 Redis 共享状态，NOT_RUN）。

    状态机：CLOSED ->（连续失败达阈值）-> OPEN ->（冷却结束）-> HALF_OPEN
            HALF_OPEN 一次成功 -> CLOSED；一次失败 -> OPEN。
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(
        self,
        *,
        failure_threshold: int | None = None,
        cooldown_seconds: int | None = None,
    ) -> None:
        # 允许显式传入（测试友好）；否则运行时读取 settings，使 patch 生效。
        self._explicit_threshold = failure_threshold
        self._explicit_cooldown = cooldown_seconds
        self._state = self.CLOSED
        self._failures = 0
        self._opened_at: float | None = None
        self._half_open_trial = False

    def _threshold(self) -> int:
        return (
            self._explicit_threshold
            if self._explicit_threshold is not None
            else settings.AI_GATEWAY_FAILURE_THRESHOLD
        )

    def _cooldown(self) -> int:
        return (
            self._explicit_cooldown
            if self._explicit_cooldown is not None
            else settings.AI_GATEWAY_COOLDOWN_SECONDS
        )

    def is_open(self) -> bool:
        """当前是否处于熔断阻塞态（OPEN 且冷却未结束）。"""
        if self._state == self.OPEN:
            if self._opened_at is None:
                return True
            return (time.monotonic() - self._opened_at) < self._cooldown()
        return False

    def allow(self) -> bool:
        """是否放行一次调用。"""
        if self._state == self.CLOSED:
            return True
        if self._state == self.OPEN:
            if self._opened_at is None:
                return False
            if (time.monotonic() - self._opened_at) >= self._cooldown():
                # 冷却结束，进入半开，允许一次试探。
                self._state = self.HALF_OPEN
                self._half_open_trial = True
                return True
            return False
        # HALF_OPEN：单次试探用完前放行，用完后需先记录结果。
        if self._half_open_trial:
            self._half_open_trial = False
            return True
        return False

    def record_failure(self) -> None:
        if self._state == self.CLOSED:
            self._failures += 1
            if self._failures >= self._threshold():
                self._state = self.OPEN
                self._opened_at = time.monotonic()
        elif self._state == self.HALF_OPEN:
            # 半开试探失败 -> 重新熔断。
            self._state = self.OPEN
            self._opened_at = time.monotonic()
            self._half_open_trial = False

    def record_success(self) -> None:
        if self._state == self.HALF_OPEN:
            self._state = self.CLOSED
            self._failures = 0
            self._half_open_trial = False
        elif self._state == self.CLOSED:
            self._failures = 0

    def reset(self) -> None:
        """测试用：清空状态。"""
        self._state = self.CLOSED
        self._failures = 0
        self._opened_at = None
        self._half_open_trial = False


class CallBudget:
    """单次请求（request_id）的调用预算（生产应持久化，NOT_RUN）。

    约束调用次数（AI_REQUEST_MAX_CALLS）与累计 token（AI_REQUEST_MAX_TOKENS）。
    consume 超额时返回 False 且不扣减，便于上层直接降级。
    """

    def __init__(
        self,
        *,
        request_id: str,
        max_calls: int | None = None,
        max_tokens: int | None = None,
    ) -> None:
        self.request_id = request_id
        self.max_calls = (
            max_calls if max_calls is not None else settings.AI_REQUEST_MAX_CALLS
        )
        self.max_tokens = (
            max_tokens if max_tokens is not None else settings.AI_REQUEST_MAX_TOKENS
        )
        self._used_calls = 0
        self._used_tokens = 0

    def consume(self, *, calls: int = 1, tokens: int = 0) -> bool:
        if self._used_calls + calls > self.max_calls:
            return False
        if self._used_tokens + tokens > self.max_tokens:
            return False
        self._used_calls += calls
        self._used_tokens += tokens
        return True

    def remaining(self) -> tuple[int, int]:
        return (
            max(0, self.max_calls - self._used_calls),
            max(0, self.max_tokens - self._used_tokens),
        )


# --- 模块级单例（生产应按租户/用户隔离，NOT_RUN） ---------------------------
_breaker = CircuitBreaker()
_budgets: dict[str, CallBudget] = {}


def _get_budget(request_id: str) -> CallBudget:
    budget = _budgets.get(request_id)
    if budget is None:
        budget = CallBudget(request_id=request_id)
        _budgets[request_id] = budget
    return budget


async def _call_ai_provider(prompt: str, *, model: str | None) -> str:
    """调用点桩：尚未接入供应商（NOT_RUN）。

    启用 AI 时此函数抛 NotImplementedError，由 call_ai_safe 捕获并降级。
    接入真实供应商时在此实现（读取 settings.AI_BASE_URL / AI_API_KEY / AI_MODEL）。
    """
    raise NotImplementedError("AI 供应商尚未接入（NOT_RUN）")


async def call_ai_safe(
    prompt: str, *, request_id: str, model: str | None = None
) -> str | None:
    """受熔断与预算约束的安全 AI 调用。任何异常/超时均优雅降级返回 None。

    - AI_ENABLED=False：立即返回 None，绝不发起外部请求、绝不抛异常。
    - 启用时：受 CircuitBreaker + CallBudget 约束，用 asyncio.wait_for 包裹调用点；
      异常/超时返回 None 并 record_failure；成功 record_success 并返回文本。
    """
    if not settings.AI_ENABLED:
        return None

    if _breaker.is_open() or not _breaker.allow():
        return None

    budget = _get_budget(request_id)
    # 粗略 token 估算（无 tokenizer 时占位）；真实接入应取供应商返回用量。
    est_tokens = max(1, len(prompt) // 4)
    if not budget.consume(calls=1, tokens=est_tokens):
        return None

    try:
        result = await asyncio.wait_for(
            _call_ai_provider(prompt, model=model),
            timeout=settings.AI_GATEWAY_TIMEOUT_SECONDS,
        )
        _breaker.record_success()
        return result
    except Exception:
        # 超时 / 供应商错误 / 未接入桩：统一优雅降级。
        _breaker.record_failure()
        return None
