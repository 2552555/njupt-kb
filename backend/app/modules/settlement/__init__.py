"""settlement 模块：结算账本、付款与财务对账（用户权责体系 §12、§14.1、§14.4、§15.5、§16.2/§16.3）。

拥有表：`settlement_statements`、`settlement_items`、`payouts`、`payout_attempts`、
`recovery_claims`、`tax_records`。

边界（§2.4、§12.1）：
- 「待结算收益」不是平台钱包：不提供充值、用户间转账、余额消费或可流通资金账户，
  只记录合同项下应付报酬。
- 不接任何真实支付渠道、不新增伪回调；`payout_attempts` 的成功/失败/不确定结果
  只由管理员凭真实证据核对写入（§12.3、§17.4）。

阶段开关：`COMMERCE_ENABLED=False` 或 `COMMERCE_COMPLIANCE_GATE_CLOSED=True` 时，
一切产生资金副作用的写接口抛 `app.core.errors.NotEnabled`（HTTP 503 / `FEATURE_NOT_ENABLED`），
不返回「成功但未实现」；只读查询仍可用虚构数据返回。
没有任何默认结算条款：`REVENUE_DEFAULT_CREATOR_BPS=0`、`SETTLEMENT_MIN_PAYOUT_FEN=0`
均表示「未配置」，未配置时抛 `NotEnabled`，不套用 80/20、85/15 或任何示例比例。
"""
