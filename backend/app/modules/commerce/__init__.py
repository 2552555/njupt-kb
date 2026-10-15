"""commerce 模块：P3 订单、支付、退款、账本、结算与提现（设计文档 §24.2、§24.3）。

拥有表：`products`、`prices`、`orders`、`order_items`、`payment_attempts`、
`refunds`、`settlements`、`ledger_entries`、`withdrawals`。

阶段开关 `settings.COMMERCE_ENABLED` 关闭时，一切会产生资金副作用的写接口必须抛
`app.core.errors.NotEnabled`（HTTP 503 / `FEATURE_NOT_ENABLED`），不返回"成功但未实现"（§25.2）。
只读查询（产品、订单详情、退款、对账、账本）不涉及资金，仍可正常工作。

支付渠道适配器见 `channels.py`；业务规则只写在 `service.py`，`router.py` 只做解析与调用。
"""
