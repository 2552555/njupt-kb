"""creator 模块：P2 贡献记录与贡献者资料；P3 收益/账本/结算/提现的对外入口。

拥有表：`creator_profiles`、`contributions`。

贡献者资料与普通账号分离（§24.1）：展示用昵称、简介等放在 `creator_profiles`，
不覆盖 identity 的账号档案。贡献分用于展示与运营，不承诺兑换现金（§24.1、§5.10）。

P3 的 `settlements`、`ledger_entries`、`withdrawals` 表由 commerce 拥有；
本模块的 `/me/ledger`、`/me/withdrawals`、`/admin/settlements/run` 通过函数体内局部导入
调用 commerce 服务（docs/ARCHITECTURE.md §2.3），不直接读写 commerce 的表。
"""
