"""支付渠道适配器（设计文档 §24.3、D12）。

本文件只定义**最小渠道协议**与一个未配置实现，不编造任何具体渠道的签名算法或字段：
渠道选择属于待确认项 D12，尚未确定。

接入真实渠道时在此实现，并需按 §24.3 最后一段补充：
- 渠道级状态映射（渠道的 pending / success / closed / refund 等 → 本地
  `PaymentAttemptStatus` / `RefundStatus`）；
- 完整账务示例（收款、分成、退款冲正的借贷分录）；
- 退款与对账用例（延迟通知、重复通知、渠道已成功本地待确认、金额/币种不一致）。

`UnconfiguredChannel` 对任何操作都抛 `NotEnabled("支付渠道未配置")`，
因此 `COMMERCE_ENABLED=false` 或渠道未接入时，回调接口返回 503 而不是假成功（§25.2）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from app.core.errors import NotEnabled

__all__ = [
    "CallbackEvent",
    "ChannelPayment",
    "ChannelQueryResult",
    "ChannelRefund",
    "PaymentChannel",
    "UnconfiguredChannel",
    "get_channel",
    "register_channel",
]


@dataclass(frozen=True)
class ChannelPayment:
    """渠道侧创建的支付请求结果。"""

    channel_reference: str
    channel_txn_id: str | None
    status: str  # 渠道原始状态，由适配器映射


@dataclass(frozen=True)
class CallbackEvent:
    """验签通过后归一化的渠道回调事件。

    `to_payload()` 产出带 `signature_verified=True` 标记的字典，供
    `commerce.service.confirm_payment` 校验"只接受验签通过的通知"（§24.3）。
    """

    order_ref: str
    channel_txn_id: str
    amount: Decimal
    currency: str
    status: str  # succeeded / failed / pending
    raw: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return {
            "signature_verified": True,
            "order_ref": self.order_ref,
            "channel_txn_id": self.channel_txn_id,
            "amount": str(self.amount),
            "currency": self.currency,
            "status": self.status,
            "raw": self.raw,
        }


@dataclass(frozen=True)
class ChannelQueryResult:
    """主动查单结果，用于对账与"渠道已成功、本地待确认"的恢复（§24.3）。"""

    channel_txn_id: str | None
    status: str
    amount: Decimal | None
    currency: str | None


@dataclass(frozen=True)
class ChannelRefund:
    channel_refund_id: str
    status: str


@runtime_checkable
class PaymentChannel(Protocol):
    """最小支付渠道协议：创建支付、验签回调、查单、发起退款。"""

    name: str

    async def create_payment(
        self,
        *,
        order_ref: str,
        amount: Decimal,
        currency: str,
        subject: str,
        return_url: str | None = None,
    ) -> ChannelPayment: ...

    def verify_callback(
        self, *, headers: Mapping[str, str], raw_body: bytes
    ) -> CallbackEvent:
        """校验渠道签名并归一化回调；验签失败抛 Forbidden，未配置抛 NotEnabled。"""
        ...

    async def query_payment(self, *, channel_txn_id: str) -> ChannelQueryResult: ...

    async def create_refund(
        self, *, channel_txn_id: str, amount: Decimal, currency: str, reason: str
    ) -> ChannelRefund: ...


class UnconfiguredChannel:
    """未接入的渠道：任何操作都显式失败，绝不返回"成功但未实现"（§25.2）。"""

    def __init__(self, name: str) -> None:
        self.name = name

    def _fail(self) -> NotEnabled:
        return NotEnabled("支付渠道未配置", details={"channel": self.name})

    async def create_payment(self, **_: Any) -> ChannelPayment:
        raise self._fail()

    def verify_callback(self, **_: Any) -> CallbackEvent:
        raise self._fail()

    async def query_payment(self, **_: Any) -> ChannelQueryResult:
        raise self._fail()

    async def create_refund(self, **_: Any) -> ChannelRefund:
        raise self._fail()


_REGISTRY: dict[str, PaymentChannel] = {}


def register_channel(channel: PaymentChannel) -> None:
    """注册已接入的渠道适配器；接入真实渠道时在模块装配处调用。"""
    _REGISTRY[channel.name] = channel


def get_channel(name: str) -> PaymentChannel:
    """返回渠道适配器；未注册时返回 `UnconfiguredChannel`（所有操作抛 NotEnabled）。"""
    return _REGISTRY.get(name) or UnconfiguredChannel(name)
