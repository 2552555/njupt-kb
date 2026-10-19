# 变更记录

## 0.0.2 — 2026-10-18

按用户确认的本地发布范围收口规划设计，并纳入原工作区 beta16。完整目录对照、升级步骤与未验收边界见 [RELEASE_0.0.2](docs/RELEASE_0.0.2.md)。

- 审核与发布分离，新增独立发布队列与发布前扫描/许可复核；贡献者暂停、恢复、本人退出及受托分配撤销。
- 权限按最终决定简化为“全站后台职责 + 课程/资料维护分配”；旧范围记录保留且不自动提权；有效权限同步到业务服务及前端导航，审核决定检查任务分配。
- 新增验证器 MFA、一次性恢复码、会话轮换、其他设备撤销和生产敏感操作的近期认证；找回密码不移除 MFA。独立加密密钥未配置时不能绑定。
- 协议/价格状态通知及确认界面、本人结算确认/明细异议、后台结算/付款核对；不确定付款在原尝试上收口，不发起外部转账。商业化与 AI 仍关闭。
- SSE 复查会话，响应开始前断连也只释放一次名额；审计导出留痕失败不下发文件，登录回跳拒绝跨站和编码绕过。
- 增加保留期调度（默认关闭）、分队列与隔离部署模板、备份恢复工具、公开产物扫描与 CI；修复 Docker 忽略迁移脚本目录及 Worker 默认队列不一致。
- 修复前后端运行依赖与传递依赖漏洞，后端固定到本次验证版本；派生版本为 `derive/3`。保留原件与历史派生。
- 迁移新增 `0012_v002_enum_checks` 与 `0013_v002_mfa`，单一 head；版本统一为 `0.0.2`。旧测试/阶段记录中的 beta 版本保留供追溯。

本轮为本地代码发布，不表示远程推送、生产部署、真实支付/模型接入、全站无障碍或真实依赖/高可用验收完成。最终本地验证证据见发布记录；前端开发工具链仍保留 5 项关联 high 告警，未使用不兼容的强制降级。

## 0.0.1-beta.16 — 2026-10-18

把「统一搜索、管理端分析与审计导出、通知实时流」三块新能力收进一轮，同时收口 beta15 明确留下的四处边界（资源分配未纳入撤回与上传、案件未联动侵权通知状态、授权定价未写域事件、价格无否决路径）。新增 `search_queries` 一张表，迁移 `0011_beta16_search`。

- **统一搜索（ALLIN §22）**：新增 `search` 模块与 `GET /search`（一次返回课程、资源、创作者三组）、`GET /search/suggest`（输入联想，前缀优先）、`GET /search/hot`（窗口内热词）。资源结果**强制**经 `access.visibility.visible_resource_ids` 过滤，只取已发布且有发布版本的资源；创作者只列至少有一个已发布资源的人，其 `resource_count` 同样按可见性统计。旧资源搜索（含学院、培养方案、教师、学年筛选）迁至 `GET /search/resources`，参数与返回结构不变，仅路径变化。归一化（去空白、转小写、截断到 `SEARCH_QUERY_MAX_CHARS`）后写入 `search_queries`，**只存词、分组、结果数与零结果标记，不存用户标识、IP、UA 或原始输入**；写日志失败不影响搜索响应。建议端点不写日志（按键级写入会放大），热词排除零结果与单字词。新增 `python -m app.modules.search.retention` 清理超过 `SEARCH_LOG_RETENTION_DAYS` 的日志，只删日志不动业务表。
- **管理端数据分析（ALLIN §43、§45）**：新增 `GET /admin/analytics/overview?days=`（`metrics.view`）。按 UTC 自然日分桶、窗口内**补零**，12 个指标（新增用户、新增资料、发布版本、下载授权、收藏、评分、评论、举报、纠错、求资料、案件）各一条 `GROUP BY` 聚合，查询数与 `days` 无关；另返回窗口总计、五段漏斗（上传会话 → 提交版本 → 审核通过 → 发布资源 → 下载授权）、下载量前 10 课程与口径说明 `data_notes`。只读聚合，不写库、不建定时任务、不自动处置。`days` 超 `ANALYTICS_MAX_WINDOW_DAYS` 直接拒绝。
- **审计导出（ALLIN §44）**：新增 `GET /admin/audit/export`（`audit.view`），输出 `text/csv; charset=utf-8` + UTF-8 BOM，列只含时间、动作、操作者邮箱与角色、对象类型/ID、请求 ID，**不导出 before/after 原文**，避免敏感变更值随文件外泄。`action` 前缀匹配、`to` 必须晚于 `from`、行数受 `AUDIT_EXPORT_MAX_ROWS` 约束并在 `X-Export-Truncated` 标注截断。以 `= + - @` 开头的单元格前置单引号防 Excel 公式注入。导出动作本身写一条 `audit.export` 审计（含筛选条件、行数、是否截断）；审计写失败不让导出 500。
- **通知实时流（ALLIN §33、buchong 实时推送待办）**：新增 `GET /notifications/stream`（SSE）。连接后立即推一次未读数，之后每个心跳先 `ping` 再复查未读，**变化时才**补推 `unread`；到 `NOTIFICATION_STREAM_MAX_SECONDS` 正常结束。**只推未读数、不推通知正文**，正文仍由受权接口拉取，连接被共享也不泄露内容。每次复查用短生命周期会话，长连接不占住连接池；进程内连接计数在 `finally` 释放，正常结束、异常、客户端断开都归还。开关关闭抛 `NotEnabled`，超限返回 409 并提示可改用轮询。前端 `useNotificationStream` 收到 `unread` 只更新未读缓存，不整页刷新、不重播入场动画；503/409/网络错误**静默降级为轮询**，不打扰用户；页面隐藏时断开、可见时重连。
- **beta15 收口 · 资源分配**：`withdraw_resource` 在 owner 与管理员之外**追加**该资源上有效的 `MANAGE` 分配判定；`create_upload_session` 在 owner 之外追加 `MAINTAIN` 分配，使受管资料管理员能为受管资源上传。两处都不放宽后续「提交版本」的既有权限规则，无权限仍统一 404 不泄露资源是否存在，上传会话的 `uploader_id` 仍记实际创建人。
- **beta15 收口 · 案件与侵权通知**：后台追加案件事件时，若案件关联了侵权通知，按 `FORWARDED → 已转送`、`DECIDED → 已决定`、`CLOSED → 已关闭` 联动通知状态，只前进不回退、已是终态不覆盖（幂等），未关联通知时静默跳过；联动写审计并注明「不代表侵权认定」。
- **beta15 收口 · 授权与定价域事件**：`DomainEventType` 新增 `agreement.accepted`、`price.proposed`、`price.confirmed`、`price.rejected` 四个取值。协议每次主体确认、价格提案/重建、双方同版本同哈希确认、价格否决各写一条域事件，`dedupe_key` 按「版本 + 主体」或提案 ID 去重，重放不重复投递；payload 只放必要 ID 与状态，不放哈希、身份证明或金额明细。新增 `POST /price-proposals/{id}/rejections`（权利方本人或 `price.confirm`），使「否决」成为真实路径；`PriceProposalState` 本轮冻结，否决与提案方撤回共用 `WITHDRAWN` 终态，差别由审计与 `price.rejected` 事件区分，历史金额不被覆盖。通知侧已登记四个事件的分发，但 `NotificationType` 本轮冻结（加值需迁移），**商业化关闭期间只落事件不造站内通知**，处理器 docstring 写明枚举扩充后的收件人规则。
- **前端**：搜索页改为统一搜索——防抖联想下拉（键盘 ↑↓/Enter/Escape、焦点返回）、无输入时展示热词、课程/资料/创作者三组结果与分组空态、`?q=` 可分享可刷新；保留原高级筛选，迁到 `/search/resources` 独立页。新增 `/admin/analytics`（7/14/30 天切换、指标卡、内联 SVG 趋势图不引图表库、漏斗、Top 课程、口径说明、403 无权限提示）。审计页加「导出 CSV」面板（时间范围 + action 前缀，blob 下载，文件名取 `Content-Disposition`）。通知入口接入 SSE 并静默降级。后台导航加「数据分析」。全部遵循 DNDL 直角与 `var(--dn-*)`，支持 `prefers-reduced-motion`。
- 前后端及锁文件统一 `0.0.1-beta.16`；迁移 `0011_beta16_search`（`down_revision = 0010_beta15_governance`），单一 head。

### 验证与部署边界

- 后端新增 4 个测试文件共 **53 项通过**：`test_beta16_search.py`(15)、`test_beta16_analytics.py`(14)、`test_beta16_stream.py`(11)、`test_beta16_closeout.py`(13)。覆盖：可见性过滤与私有资源不外泄、归一化命中、建议前缀与不写日志、热词排除零结果与单字、日志开关关闭、保留期清理、日志表无用户标识列；分析端点 403、窗口补零与连续日期、`totals` 等于分桶之和、漏斗非负、Top 课程排序与上限、CSV BOM/表头/截断头/公式注入防护/导出自身审计；SSE 开关 503、未登录 401、超限 409、计数回落（正常/异常两条路径）、事件格式、变化才推、`ping` 周期、到点结束、不出现正文；分配可撤回/不可撤回/过期失效、`REVIEW` 不放宽撤回、案件联动与幂等、四类授权事件与 payload 不含敏感字段。
- 前端 `tsc --noEmit`、`eslint` 通过；生产构建见下条说明。
- 迁移 `0011_beta16_search` 离线 PostgreSQL 升级 SQL 生成通过，单一 head；只新增一张表与两个索引，不改既有列语义、不回填、不改写历史数据。
- **仍为 NOT_RUN/待验收**（本机无 Docker/PostgreSQL/Redis/MinIO/ClamAV）：真实 PostgreSQL 迁移与并发、真实服务联调、Celery/真实邮件、受信代理与入口限流。**搜索在 SQLite 与 PostgreSQL 上都用 `ILIKE '%词%'`，前缀通配无法命中 B-tree 索引**，真实数据量下的性能未验证；`search_queries` 的增长与清理调度也只在测试里验证过。SSE 只在进程内计数，多进程/多实例部署需要共享计数与粘性会话，未做真实浏览器与反向代理（缓冲、超时）验证。审计导出为同步查询，大数据量下的内存与耗时未压测。
- 商业化与 AI 保持关闭：`COMMERCE_ENABLED=false` 且 `COMMERCE_COMPLIANCE_GATE_CLOSED=true`，授权/定价写接口在关闭时抛 `NotEnabled`，四类域事件在生产默认配置下不会产生；AI 不接入外部模型。本轮为本地迭代，不据此宣称已部署或发布。

### 规划对应与边界

对应《ALLIN 总览与规划索引》§22（搜索系统）、§43（数据分析）、§44（审计系统）、§45（可观测性）、§33（通知系统），《课程资料平台用户权责、内容治理与授权收益体系设计文档》§3.3（分配是 owner 之外的追加判定）、§7.1（双向定价与否决）、§13.3（案件与侵权通知状态联动），以及《规划设计/buchong.md》的实时推送待办。边界：搜索未引入外部搜索引擎，仍是数据库 `ILIKE`，中文分词与相关性排序未做；`/search` 路径从「资源搜索」改为「统一搜索」属**破坏性变更**，旧路径迁至 `/search/resources`，本仓库前端已同步，外部调用方需自行调整；热词与零结果只是观测，不据此自动调整排序或推荐；分析指标口径以各函数 docstring 为准，`published_versions` 以发布事务写入的审计为事实来源（内容表没有独立的发布时间列）；授权事件的通知类型待 `NotificationType` 扩充（需迁移）后补发；价格否决复用 `WITHDRAWN` 终态而非新增状态；SSE 不承诺跨实例、不承诺消息不丢，断线后以拉取结果为准。

## 0.0.1-beta.15 — 2026-10-17

按《课程资料平台用户权责、内容治理与授权收益体系设计文档》v1.0 与《高并发 高可用与性能工程专项设计方案》v1.0 推进：把三级 RBAC 与资源级隔离、贡献者入驻与审核补证、案件与侵权通知闭环、授权协议与双向定价、订单明细分配与账本、结算与付款核实，以及缓存/事件/指标硬化收进一轮。新增 33 张表、迁移 `0010_beta15_governance`。

- **三级 RBAC 与资源级隔离（§3、§15.2）**：新增 `admin_permission_assignments`（按职责追加的系统管理员权限，含 `scope`/`valid_until`，撤销走 `revoked_at` 不物理删除）与 `resource_assignments`（资料管理员的对象级作用范围，资源或课程二选一由 CheckConstraint 保证，`capability` 分 maintain/review/manage）。`require()` 由「角色权限」升级为「角色权限 ∪ 未过期按需授权」（`core.permissions.effective_permissions`）。新增 9 个权限常量（`contributor.review`、`assignment.manage`、`case.handle`、`rights_notice.handle`、`agreement.manage`、`price.confirm`、`ledger.view`、`payout.manage`、`metrics.view`）。新增 `/admin/permission-assignments`（授予/查询/撤销）、`/admin/resource-assignments`（授予/查询/撤销）与 `GET /me/assignments`。分配判定在 owner 之外**追加**，列表、详情、版本清单共用同一作用域过滤；审核权限仍由 `resource.review` 决定，不由分配放宽。
- **贡献者入驻与审核补证（§3.4、§5、§14.1）**：新增 `contributor_applications`（applied→under_review→active/rejected；保留 suspended/exited 但本版不实现降级）。普通用户仍可直接投稿免费内容，入驻只影响有偿销售的准入。接口 `POST/GET /me/contributor-applications`、`GET /admin/contributor-applications`、`POST /admin/contributor-applications/{id}/decisions`（`contributor.review`，理由必填，`active` 时仅做 `user → contributor` 最小提升）。`ModerationDecisionType` 与 `ReviewStatus` 新增 `needs_info`：审核可「要求补证」，置 `needs_info`、不发布、不覆盖旧结论，重投形成新的审核记录。
- **案件与侵权通知闭环（§13、§15.5）**：新增 `cases`/`case_events`/`case_measures`/`rights_notices`/`counter_statements`。用户侧 `POST /cases`、`GET /me/cases`、`GET /me/cases/{id}`（分层返回，不含内部备注、证据原文与处理人）、`POST /rights-notices`（**无需购买**即可提交，限频，身份证明仅受理人员可见）、`POST /rights-notices/{id}/counter-statements`（仅被通知方或合法代理）。后台 `GET /admin/cases`、`POST /admin/cases/{id}/events`、`POST /admin/cases/{id}/measures`、`POST /admin/cases/{id}/measures/{measure_id}/release`。措施效果：`unpublish` 置资源 `withdrawn`；`block_download`/`restrict_preview` 复用 access 撤权语义（只改状态、不删凭证）；`block_sales`/`freeze_revenue` 在商业化关闭时只记录并标注未启用；**release 不自动重新公开**。反通知期限按 `RIGHTS_NOTICE_COUNTER_DEADLINE_DAYS` 写入 `deadline_at` 作为记录，不宣称所有案件统一适用十五日。
- **授权协议与双向定价（§6—§8、§15.3）**：新增 `license_agreements`/`agreement_versions`/`agreement_acceptances`/`price_proposals`/`price_acceptances`/`revenue_rule_versions`/`product_composition_versions`/`product_components`/`free_license_acceptances`。双方确认**同版本同哈希**价格才生效；改价重建版本；平台不可代贡献者确认；`creator_bps` 必须显式给出（0–10000）且发布后不可改；组件权重非负且至少一项正数，缺组件许可时上架失败。导出 `assert_sellable`（无有效许可/确认价格/收益规则不得创建新付费订单）。
- **订单明细分配与账本（§9—§11、§15.4/§15.5）**：`orders`/`order_items` 增加快照列（全部可空，仅 `refunded_fen` 为 NOT NULL DEFAULT 0 以兼容既有行）；新增 `order_item_component_allocations`/`order_item_allocations`/`grant_components`/`refund_items`/`refund_component_allocations`/`refund_beneficiary_reversals`/`ledger_accounts`/`ledger_journals`/`ledger_lines`/`allocation_reservations`。服务：`allocate_order_item`（金额守恒、分尾差确定性）、`accrue_revenue`（借贷相等、`event_key` 全局唯一保证一次事件不重复计提）、`reverse_revenue_for_refund`（累计冲回 ≤ 原可冲回，平台冲回只计一次）、`reserve_allocation`/`release_reservation`、`settleable_balance`。锁顺序统一为「用户交易锁 → 订单 → receipt/claim/refund → grant 与会员段 → allocation（ID 排序）→ 结算单/payout → ledger_accounts（ID 排序）」。明细退款 `scope=ORDER_ITEMS` 恒返回 `REFUND_SCOPE_UNSUPPORTED`，**不静默退整单**；`FULL_ORDER` 原语义不变。
- **结算与付款（§12、§15.5）**：新增 `settlement_statements`/`settlement_items`/`payouts`/`payout_attempts`/`recovery_claims`/`tax_records`。接口：本人结算单（账户脱敏）/确认/异议；后台生成结算草稿（按成熟与预留过滤，预留金额不进新批次）、批准锁批、付款 attempt、核对 `succeeded`/`failed`/`uncertain`（证据必填、`external_ref_key` 唯一、`uncertain` 持续占款）、账务更正（关联原记录的反向分录，不直接改余额）。`reconcile_pending_payouts` 只读核对，**不自动重放外部转账**。
- **缓存、事件与指标硬化（高并发专项 §8、§10、§12）**：新增 `core/cache.py`，按 `CacheClass` 分级的有界 TTL 缓存——`public_read` 回源失败返回旧值或 None 不抛出（有界回源，避免雪崩放大）、`viewer_scoped` 短 TTL 且支持写后失效、`auth_sensitive`/`no_cache` **永不缓存**（权限、会话撤销与硬配额不因缓存故障放行）、`CACHE_MAX_ENTRIES` 上限 + LRU 淘汰。新增 `notification/reconcile.py`（`reconcile_events` 只读统计积压/毒任务/重复 `dedupe_key`；`replay_failed_events` 幂等重排）。新增只读 `GET /admin/metrics`（`metrics.view`，开关关闭抛 `NotEnabled`）：连接池预算、缓存统计、域事件积压、任务积压与慢查询阈值，不写库、不自动处置。
- **通知与前端**：为 beta15 新增的 10 类域事件登记通知处理器（补证、入驻、权限变更、案件、侵权通知），处理人本人不收到自己的通知，事件重试不重复投递。前端新增 9 个页面（`/me/contributor-application`、`/me/cases`、`/me/cases/[id]`、`/rights-notice`、`/me/agreements`、`/admin/cases`、`/admin/contributor-applications`、`/admin/assignments`、`/admin/metrics`）与对应导航入口，审核页新增「要求补证」，全部遵循 DNDL 与 `prefers-reduced-motion`。
- 前后端及锁文件统一 `0.0.1-beta.15`；迁移 `0010_beta15_governance`（`down_revision = 0009_beta13_retention`），单一 head。

### 验证与部署边界

- 后端隔离 SQLite 全量 **337 项通过**（beta14 基线 233 项 + beta15 新增 104 项）；beta15 新增测试文件：`test_beta15_rbac.py`(14)、`test_beta15_cases.py`(17)、`test_beta15_cache.py`(11)、`test_beta15_ops_metrics.py`(8)、`test_beta15_creator.py`(11)、`test_beta15_review_needs_info.py`(7)、`test_beta15_licensing.py`(14)、`test_beta15_commerce_alloc.py`(9)、`test_beta15_settlement.py`(13)。
- 迁移 `0010_beta15_governance` 离线 PostgreSQL 升级 SQL 生成通过，单一 head；33 张新表均为增量新增，不改既有列语义、不回填、不改写历史数据。`require()` 改为按有效权限判定后，`test_beta14_security` 的依赖直调用例同步传入会话（签名变更的适配，断言不变）。
- 前端 `tsc --noEmit`、`eslint` 与生产构建通过。
- **仍为 NOT_RUN/待验收**（本机无 Docker/PostgreSQL/Redis/MinIO/ClamAV）：真实 PostgreSQL 迁移与并发（行锁、唯一约束在真实引擎下的行为）、对象存储与扫描联调、Celery/真实邮件、受信代理与入口限流、真实收款/退款/贡献者付款闭环。SQLite 与离线 SQL 不能代替这些验证。
- 商业化保持关闭：`COMMERCE_ENABLED=false` 且 `COMMERCE_COMPLIANCE_GATE_CLOSED=true`；`REVENUE_DEFAULT_CREATOR_BPS=0`、`SETTLEMENT_MIN_PAYOUT_FEN=0` 表示「未配置」，未配置时报价/结算抛 `NotEnabled`，不产生任何默认商业条款。AI 保持关闭。本轮为本地迭代，不据此宣称已部署或发布。

### 规划对应与边界

对应《用户权责、内容治理与授权收益体系设计文档》v1.0 的 §3（三级 RBAC 与资源级隔离）、§5（投稿与审核）、§13（争议与侵权通知）、§6—§8（授权、定价与商品）、§9—§12（订单、收益、退款、结算）、§14（状态机与不变量）、§15（数据库设计）、§16（API 与事务契约），以及《高并发 高可用与性能工程专项设计方案》v1.0 的 §8（缓存与撤权一致性）、§10（任务与事件可靠性）、§12（故障降级）、§13（监控告警）。边界：G1—G8 上线门槛未核实，生产收费与贡献者结算不开放；`Role.ADMIN` 的收窄模式未启用（仍为全权限）；`resource_assignments` 未纳入 `withdraw_resource` 与上传会话创建；`role` 提升只做 `user → contributor` 最小变更；对 ORDER/OTHER 类案件目标无法核验被通知方时不阻断；后台案件事件仅推进案件状态机，未联动 `rights_notices.status`；明细退款与多贡献者合单按 §18.3 留待第二阶段，本版恒返回 `REFUND_SCOPE_UNSUPPORTED`；licensing 未写域事件（`DomainEventType` 无对应类型）；项目无 SKU 与 `parties` 表，定价挂 `products`、主体用 `users.id` 加平台哨兵；金额用 CNY 整数分。NGINX/PostgreSQL 参数/Redis 部署/k6 压测等纯基础设施属拟实施示例，本机无 Docker 未落地。

## 0.0.1-beta.14 — 2026-10-16

批量补齐资料发现、账号安全、本人治理进度与安全运营；收口工作区已有 beta14 改动，继续沿用现有模块、权限和事件机制。

- **资料发现与统计**：资料聚合页新增入口、关键词/类型筛选及最新/最早/热门排序；课程资料支持排序、游标翻页，创作者资料支持翻页。资料需求榜展示近 7 天开放需求并按关注人数排序。热门资源按收藏与已消费下载授权的时间衰减分排序，首屏时间写入游标，避免后续页因时间流逝重复；互动事实变化仍可能改变排序。课程分类计数遵循当前查看者的元数据可见范围；详情展示收藏、评分和已消费下载授权数（不代表文件实际传输完成次数）。推荐增加培养方案的专业/学院相关信号，资料缺项时正常回退；阈值和排序参数仍是可调整默认值。
- **账号与会话**：登录态修改密码校验旧密码及既有强度要求，保留当前会话并撤销其他会话；新增一键退出其他设备。登录成功时按用户行锁收敛活跃会话上限，过期会话不占上限；默认 5，可配为 0 关闭。登录失败审计在独立事务保留，不被失败请求回滚，也不提交请求中的其他修改。
- **本人举报与申诉闭环**：新增 `/me/reports`、单条详情、状态时间线、`/me/comment-appeals`，前端统一到“举报与申诉”。类型/状态筛选和游标分页；只返回本人记录，管理员也无跨用户读取特权。下架、草稿、撤权目标遮蔽资源/评论/版本标识与标题，仍可查看本人理由和工单状态；不返回内部备注、证据或处理人。评论举报创建和处理在原事务内追加状态事件，无需新表或迁移。
- **通知与运营**：纠错结果统一由 `correction.resolved` 异步事件生成，携带资料/版本跳转数据，处理人就是提交人时不通知自己；移除同步重复入口，事件重试不会重复投递。新增管理员安全概览：近 1 小时登录失败、近 24 小时新 IP 登录、近 7 天下载速率标记。下载速率检测只记审计，不自动冻结，也不替代下载硬配额；Redis 故障时进程内计数不代表跨进程风控保证。
- **页面与恢复**：搜索零结果到求资料、登录回跳保留关键词和课程；补齐通知/收藏等错误重试与空态、全局错误边界。新增隐私/条款/版权草案入口，明确尚未生效及运营事项待确认。新筛选 URL 对重复查询参数统一取首值，避免数组参数导致整页崩溃。
- 前后端及锁文件统一 `0.0.1-beta.14`，迁移 head 保持 `0009_beta13_retention`；保留原有工作树和修改前本地快照。

### 验证与部署边界

后端隔离 SQLite 全量 **233 项通过**（使用 asyncio Runner 关闭调试追踪以避开 Windows 测试开销，不改变测试断言）；变更 Python 文件 Ruff、`git diff --check` 通过；Alembic 单一 head，PostgreSQL 全链升级 SQL 生成通过。本轮无新增迁移。前端 TypeScript、全量 ESLint 与最终 beta14 生产构建通过（41 个静态页面；首次 Windows `.next` EPERM 后以正常权限复跑成功）。Chromium 明确接口测试数据验收 8 组通过、零页面运行异常，覆盖本人举报/申诉与轨迹、改密及退出其他会话、安全概览、搜索登录回跳、课程/资料翻页及重复参数；1365px/390px 六类页面无文档横向溢出，14 张截图已保存并检查代表页面。独立 Python/TypeScript 审查已完成，发现的问题已修复并回归。真实 PostgreSQL 迁移/并发、MinIO、ClamAV、Celery/真实邮件、受信代理及生产安全验收仍待执行；SQLite、浏览器接口测试数据与构建不能代替这些验证。本轮为本地迭代，不据此宣称已推送、部署或发布。AI 与商业化保持关闭，不接入外部模型，不改变原件授权、审核或申诉决定规则。

## 0.0.1-beta.13 — 2026-10-16

把治理队列防滥用、运营告警、预览撤权与注销账号保留期清理收进一轮，并补上讲安全更新：预览短期化与水印防篡改、AI Gateway 熔断/预算构建块、API 安全响应头与生产防泄露、依赖基线升级。

- **治理队列防滥用（buchong.md）**：评论举报与作者申诉新增按用户的提交频控与未处理上限，可调默认值（`COMMENT_REPORT_*`、`COMMENT_APPEAL_*`）。按用户维度计数，未处理数达到上限即拒绝新提交，避免单一账号刷爆治理队列；不改变受理/驳回/处置的既有权限与审计。
- **运营告警只读聚合**：新增 `GET /admin/alerts`（`ops.view`），复用工作台计数口径聚合未处理举报、待审版本、未处理纠错、开放需求、失败事件与失败任务，按可调阈值（`OPS_ALERT_*`）产生 warning/critical 两级告警，critical 走 `OPS_ALERT_CRITICAL_MULTIPLIER` 倍率。只读聚合、不写库、不自动处置。
- **预览撤权硬化（§17.1）**：新增 `POST /admin/preview-grants/{grant_id}/revoke`（单条）与 `POST /admin/resources/{resource_id}/preview-revoke`（按资源批量，仅撤活跃态），均需 `RESOURCE_REVIEW` 权限、理由必填 1–255 字、写审计并清预览缓存；已撤销的幂等报 `Conflict`，不删凭证记录。
- **注销保留期清理**：新增迁移 `0009_beta13_retention`（`users.purged_at` + 索引）；`identity.run_retention_cleanup` 对超过保留期（默认 `ACCOUNT_RETENTION_DAYS=30`）的已注销账号置墓碑邮箱 `purged+{id}@purged.invalid`、清邮箱验证时间、撤销全部会话与 EMAIL 身份，置 `purged_at`；**不删用户行、业务记录与审计**，仅收敛个人可识别信息。提供 `python -m app.modules.identity.retention` 定时清理入口。

安全更新（对应 prevew.md、AI对抗攻击防御设计.md、rongyu.md、代码保护与网络安全补充规范）：

- **预览与传输安全**：预览专用短期预签名 `storage.presign_preview_download`，改用 `PREVIEW_URL_TTL_SECONDS`（默认 300s）替代默认下载有效期，`issue_preview_grant` 据该 TTL 设置 `grant.valid_until`；新增 `access.watermark_integrity`，以 `SECRET_KEY` 的 HMAC-SHA256 把水印令牌绑定到 (用户, 资源, 版本)，预览结果新增 `watermark_token` 字段供渲染侧复核防篡改/冒用。
- **AI 安全构建块**：AI 默认关闭；启用前提供 P0 边界 `core/aigateway.py`——`CircuitBreaker`（连续失败熔断、冷却后半开试探）、`CallBudget`（每请求调用次数/累计 token 预算，`AI_REQUEST_MAX_*`）、`sanitize_ai_prompt`（剥离控制字符与提示注入越权标记）、`call_ai_safe`（`AI_ENABLED=False` 立即空操作返回 None，启用时受熔断/预算/超时 `AI_GATEWAY_*` 约束并优雅降级）。未接入线上供应商、熔断/预算不做跨进程共享（均为进程内，生产应接 Redis）。
- **代码与网络安全**：新增 `SecurityHeadersMiddleware`（注册在最外层）为 API 响应下发 `X-Content-Type-Options`、`X-Frame-Options: DENY`、`Referrer-Policy: no-referrer`、`Permissions-Policy`、CSP `default-src 'none'`、`X-XSS-Protection: 0`、`Cache-Control: no-store`，生产（`cookie_secure`）另加 HSTS；生产环境非 HTTPS（含反向代理 `X-Forwarded-Proto`）返回 403。`errors.py` 增加通用 500 处理器：生产只返回通用的 `INTERNAL_ERROR`，不泄露堆栈/路径/配置，dev 保留详情。前端 `next.config` 增 `headers()` 下发非破坏性浏览器安全头（prod HSTS）。
- **依赖/供应链安全**：`fastapi 0.115.6→0.115.12`（查询参数解析 ReDoS，GHSA-2m2c-j64x-2xpp）、`Pillow 11.1.0→11.3.0`（CVE-2025-48379）。保持最小升级，未动无安全补丁的依赖。

新增 `tests/test_beta13.py`（治理防滥用/运营告警/预览撤权/保留期 11 项）与 `tests/test_security_preview.py`、`tests/test_aigateway.py`、`tests/test_security_headers.py`；前后端及锁文件同步 `0.0.1-beta.13`；`docs/INTERFACES.md` 增加 beta13 增补节。

### 验证与部署边界

- 后端隔离 SQLite 全量 **177 项通过**（原 144 项 + beta13 11 项 + 安全 22 项），覆盖举报/申诉频控与未处理上限、运营告警阈值触发/不触发、预览单条与资源级撤销及幂等、保留期清理保留业务记录、预览 TTL 与水印令牌正确/篡改判定、熔断开—冷却—半开—恢复、调用预算超额、AI 关闭时空操作、提示注入剥离、安全响应头与生产 500 防泄露、prod 明文 403 与 `X-Forwarded-Proto` 放行。
- 未做前端全量 TypeScript/ESLint/生产构建的本地复跑（`next.config` 的头为增量、非破坏性；建议 CI 复跑 `tsc --noEmit`、lint 与 build）。
- 迁移 `0009_beta13_retention` 单一 head，仅加一列一索引，回退删列删索引，无既有数据回填风险。
- **仍为 NOT_RUN/待验收**（本机无 Docker/psql/Redis/MinIO/真实 S3/AI 供应商，不伪造已完成）：真实 PostgreSQL 迁移与并发、Redis 熔断/预算跨进程共享、MinIO/真实签名短链、ClamAV/Celery 联调、AI 供应商接入、反向代理 TLS 强传输与受信代理配置。`aigateway` 为启用前构建块，未接入任何线上 AI。

### 规划对应与边界

对应系统设计 §13/§15/§17/§25/§27，以及 `规划设计/prevew.md`（签名 URL 短期化、水印防篡改）、`规划设计/AI对抗攻击防御设计.md` 与 `规划设计/rongyu.md`（防自动化、提示注入、熔断/预算、AI 是增强非依赖）、`规划设计/代码保护、逆向工程防护与网络安全补充规范.md`（安全响应头、传输安全、接口最小化）、`规划设计/buchong.md`（治理队列防滥用、运营告警）。边界：AI 默认关闭、不擅自接线上供应商；API CSP 为 JSON 最严策略，前端 HTML/JS 的宽松 CSP 另行白名单配置；水印令牌不带权限判断，权限仍由 authorize 负责；保留期只清理个人可识别信息，业务事实与审计一律保留；依赖按最小安全补丁升级，未做无关大版本变动。本轮为本地迭代，未据此宣称已部署或发布。

## 0.0.1-beta.12 — 2026-10-16

把高频个人数据与治理动作收进工作台，并替上传与预览链路补上解析上限和字节一致性硬约束。

- **个人总览、下载历史与关注动态**：新增 `GET /me/overview`、`/me/downloads`、`/me/feed`，只从既有业务事实聚合，不新增跟踪表。总览统计本人上传、草稿、待审、收藏、关注、未读通知、开放需求/纠错、下载次数，以及作为创作者收到的下载授权、评分与均分；下载历史按授予时间降序游标分页，资料转私有、下架或发布新版本后，该行标记不可用并遮蔽资料 ID/标题/文件名/版本号，不泄露已失去访问权的内容；关注动态合并课程与创作者关注、按当前已发布版本去重。前端 `/me` 从跳转改为个人总览（含创作数据），新增 `/me/feed`、`/me/downloads`，账号菜单与移动端「我的」改为总览入口。
- **管理后台补齐治理入口**：`GET /admin/users` 支持关键词/状态/角色过滤与游标分页，不返回口令等敏感字段。暂停/恢复走 `moderation.set_account_restriction`（理由必填、撤销全部会话、写审计、已注销账号不可恢复），账号状态改为 `identity.set_account_status` 唯一写入口（行锁、仅管理员、不能操作自己与他人管理员、状态流向 active↔suspended）。新增 `/admin/users`（用户治理）、`/admin/entitlements`（访问权益）、`/admin/jobs`（异步任务）、`/admin/audit`（审计记录）四个页面；权益授予前核对用户与授权对象、到期必须晚于当前时间、撤销必填理由；任务页展示失败详情并支持临时失败重排队；审计页支持深链、过滤与变更前后详情。审核员可查看任务但不能管理账号或重试；后台导航、标题与工作台「异步处理」分区同步更新。
- **上传与预览链路硬化**：
  - 扫描通过后把本次实际扫描的本地字节写入私有区（`verified_storage_key`），不再复制可能被上传凭证覆盖的隔离对象；扫描或存储失败不会留下私有区快照。
  - OOXML 解析拒绝绝对路径、`..`、盘符与符号链接条目，限制压缩条目数与解包总量；宏文档大小写不敏感排除；PDF 页数与图片像素受新配置 `MAX_DOCUMENT_PAGES`/`MAX_IMAGE_PIXELS`/`MAX_ARCHIVE_ENTRIES`/`MAX_ARCHIVE_UNPACKED_BYTES` 约束（`.env.example`、docker-compose 同步）。
  - 生成预览前重新下载并校验 `size` 与 `sha256`，与扫描记录不一致即失败：不渲染、不清空既有预览；普通派生成功后才替换旧记录，水印对象按处理器版本复用。
  - `issue_preview_grant` 在首次渲染完成后重新读取授权事实再签发：渲染期间下架、关闭预览或权限变化都不会签发预览链接。
  - 对象下载超过 `MAX_UPLOAD_SIZE_BYTES` 即停止读取并关闭响应体，不写满临时磁盘。
  - PDF 缩略图按长边等比缩放并释放渲染资源，极端长宽比不会产生超大位图；图片缩略图与水印 WebP 清除元数据。
- 游标分页统一到 `core.pagination.seek_descending`（jobs、moderation、access 与 ops 列表共用），SQLite 与 PostgreSQL 排序一致；`PROCESSOR_VERSIONS` 的 `file.scan`/`file.derive` 升至 `scan/2`、`derive/2`；docker-compose 的 worker 服务补 `env_file`。
- 新增 `backend/tests/test_beta12.py` 16 项；前后端及锁文件同步 `0.0.1-beta.12`；`docs/INTERFACES.md` 增加 beta12 增补节。

### 验证与部署边界

- 后端隔离 SQLite 全量 **144 项通过**（原 128 项 + beta12 16 项），覆盖账号状态流转与拒绝项、下载历史遮蔽与分页、关注动态去重、权益三项校验与撤销、任务重入幂等、扫描快照字节一致性、下载上限中止、压缩包/页数/像素上限、极端长宽比缩略图。
- 前端 TypeScript、ESLint 与生产构建通过；Chromium 接口测试 8 组通过（总览服务端计数、动态与下载历史遮蔽、停用/恢复理由与刷新、任务失败详情与重试、权益授予核对与撤销、审计深链与前后详情、390px 七页无横向溢出、审核员可见任务不可操作），桌面与 390px 截图已检查。
- 无数据库结构变更，不新增迁移。`scan/2`/`derive/2` 会按新处理器版本重新排队既有失败任务，属预期行为；回退随应用一起回退即可，无 DDL 风险，但 beta12 新端点将 404。
- 真实 PostgreSQL、MinIO、ClamAV、Celery 联调仍未完成；解析上限为可调默认值，线程渲染超时不等于硬终止解析器进程，生产解析隔离与成本门禁仍需验收；worker 必须配置 `.env`，入口仍须校验受信代理。本轮为本地迭代，未据此宣称已部署或发布。

### 规划对应与边界

对应系统设计 §13 预览链路、§16 安全检查、§22 任务队列、§25 分页与 §27 运营后台，以及 `规划设计/yonghu.md`、`buchong.md` 中关于个人工作台与治理后台的实施建议。个人数据只做聚合展示，不新增跟踪表；权益授予不创建订单或付费记录；贡献分仍只展示，资金/结算/提现属 P3 未启用。

## 0.0.1-beta.11 — 2026-10-16

收口已有 beta11 分支，继续落实预览隔离、接口限流和求资料复核。

- **第一页水印图片**：已扫描 PDF/PNG/JPEG 为登录用户生成 WebP；限制长边、移除原件元数据，重复水印含用户短码及生成日期。用户对象以完整 HMAC 区分，复用已生成对象，不修改原件。接口新增 `preview_type`；图片加载失败可重新申请。匿名、Office、开关关闭或生成失败保留受限 PDF/缩略图回退；不声称所有格式已经纯图片化。
- **API 分级限流**：登录、搜索、课程列表/详情、资料详情、预览/下载授予、上传使用可调的角色限额。用户优先于会话/IP，避免同校园出口下的登录用户挤占；固定窗口原子计数，独立事务，超限审计与 `429`/`Retry-After`。不直接相信客户端代理头，不替代下载硬配额。
- **解决资料失效复核**：详情/列表按查看者权限遮蔽不可访问资料的 ID/标题；不自动改变需求状态。显式复核要求理由和修订号，保留“已解决发起者或管理员可重开，已关闭仅管理员”规则；复用审计和通知事件。修正身份变化后的需求缓存隔离。
- **弹窗定位**：路由动效形成 transform 容器时，旧 fixed 弹窗可能被页面裁切；改为挂到 body，保留焦点管理、Escape 与滚动锁定。
- 增加迁移 `0008_beta11_ratelimit`，前后端及锁文件同步 `0.0.1-beta.11`，环境模板补开关与限额覆盖示例。保留原 beta11 工作树和本地数据库备份；前一基线有本地备份分支。

### 验证与部署边界

- 后端隔离 SQLite 全量 **128 项通过**（原 93 项 + 预览 12 项 + 限流 11 项 + 复核 12 项）；完整 HMAC 用户隔离/复用在最终修改后另行复测 2 项通过。
- 前端 TypeScript、变更文件 ESLint、生产构建通过；后端本轮模块 Ruff 与限流/派生模块定向 Mypy 通过。审查发现的权限、缓存、存储回退、代理与类型问题已修正。
- Chromium 接口测试数据 4 组通过：图片展示与无 PDF iframe、失效/限流提示、复核理由/冲突恢复、关闭需求管理员权限；桌面和 390px 弹窗实测未超视口，截图已检查。
- 单一迁移 head；PostgreSQL 升降级 SQL 已生成，`0008` 在 SQLite 的实际升降级保留其他表及已有数据。生产 YAML 静态解析通过，API 读取 `.env` 的预览/限流配置，并要求填写受信代理地址。

真实 PostgreSQL 迁移/并发、MinIO、ClamAV、Celery 和 Office 转换尚需独立联调；本机未提供 Docker/psql。限流数值仍为可调默认值；生产必须配置 ASGI 受信代理并验证入口一致性。线程渲染超时不等于硬终止解析器进程，生产解析隔离与成本门禁仍需验收。

回退应先切换到不依赖限流表的应用，再降级 `0008`；只丢弃限流窗口，不自动删除业务事实或历史水印对象。本次为本地迭代，未据此宣称已部署或发布。

## 0.0.1-beta.10 — 2026-10-16

在 beta9 基础上收口 P2 的账号治理、目录运营与互动治理三条线。

- **账号注销与数据导出**：新增 `POST /me/deletion`，校验密码与固定确认词后立即注销——写入 `deleted_at`、匿名化个人资料、撤销全部会话；上传资源、评论、评分、审计与事件记录一律保留，评论不匿名化。新增 `GET /me/export`，导出本人数据 JSON 附件，不含文件字节、签名 URL 与他人数据。保留策略 30 天，本轮不实现自动清理任务。
- **管理员目录维护**：新增大学/学院/专业/课程/教师/学期的创建、改名、软停用与合并接口，合并先返回影响面再执行；停用项退出前台选择器与检索，已关联资料仍可读取；全程审计，不物理删除。
- **运营任务工作台**：新增 `GET /admin/workbench` 与 `ops.view` 权限，只读聚合待审版本、未处理纠错、未处理投诉/举报、开放中需求、失败事件与任务，每条指标一次 COUNT。
- **推荐探索槽位**：规则推荐保留 1 个固定探索位（`limit>=4` 时启用，候选为 30 天内且评价数 <3 的新内容），理由标注「新内容，供探索」，无候选不占位；仍走同一套可见性/发布/审核/时效过滤。
- **评论举报与申诉**：新增评论举报（理由必填、不可自举报、同一用户对同一评论至多一条未处理举报）与作者一次性申诉；管理员用 `report.handle` 受理/驳回/处置，处置复用 beta8 的评论隐藏能力，采纳申诉即恢复评论。复用 `report.created` / `report.resolved`，未新增事件类型。
- 新增迁移 `0005_beta10_account`（`users.deleted_at`）、`0006_beta10_ops`（教师/学期状态、课程状态 CHECK 增 `inactive`）、`0007_beta10_interaction`（举报关联评论、申诉表）；前后端及锁文件同步 `0.0.1-beta.10`。
- 前端新增 `/admin/workbench`、`/admin/catalog`，扩展 `/admin/reports` 处理评论举报与申诉、`/me/security` 落地注销与导出、资料讨论区增加举报与申诉入口、推荐区区分探索项。

### beta10 收尾修正（2026-10-16 晚）

beta10 三线合并后按本地实测做的一轮修复与体验收敛，不改变 beta10 的功能契约。

- **修复搜索在 SQLite 上不可用**：`GET /search` 的裸 SQL 原使用 PostgreSQL 专有的 `ILIKE` 与 `EXTRACT`，在本地/测试库（SQLite）直接 500；现按方言取等价写法（SQLite 用 `LIKE` 与 `strftime`），PostgreSQL 行为不变。同时修复三类只在 SQLite 现形的缺陷：裸 SQL 的 UUID 参数未声明类型导致 `Error binding parameter: type 'UUID' is not supported`；游标分页与学期/教师筛选同类问题；裸 SQL 读回的 uuid 列为无连字符字符串，现统一转回 `UUID` 对象。`access` 的预览派生物理同样做了读回归一。新增 4 个回归用例（关键词命中课程名、无匹配、适用年份、UUID 参数路径）。
- **修复头像上传**：`image/jpg`、`image/pjpeg`、`image/x-png` 等浏览器非标准 MIME 先归一再校验，此前 Windows 下选择 `.jpg` 会被拒；新增 GIF。前端上传改为按钮触发（含文件名回显），`accept` 同时给扩展名与 MIME。
- **界面文案精简**：删除「功能名已自明」的说明段落（搜索/推荐/工作台/目录维护/收藏/关注/通知/首页等约 30 处），保留空态操作引导、不可逆操作后果与合规声明（注销、贡献不兑换现金、下载政策、邮箱验证边界）。功能由界面本身表达，不靠提示解释。
- **品牌标识**：页头去掉「邮」字色块，改为「南京邮电大学 / 课程知识平台」排版式标识；未获得校方授权前不虚构校徽。
- **动效**：纳入 DNDL `motion.css`，新增路由切换顶部进度条、路由入场（`(app)/template.tsx`）、列表逐项进入（`dn-stagger`，新节点才播放、数据刷新不重播）、骨架屏横向微光与按钮 hover/press 反馈；全部遵循 `prefers-reduced-motion`。

### 本地验证（收尾修正后）

- 后端全量 93 项测试通过（隔离内存 SQLite）：beta10 的 89 项 + 搜索方言回归 4 项。
- 本地全接口实测脚本（`local-dev/audit_local.py`）**290/290 通过**：三角色登录、搜索全部分组与翻页、推荐与探索位、工作台计数与库内一致、目录维护增删改合并、举报→处置→申诉→恢复闭环、收藏/评分/关注、求资料回应、导出与注销（含错误密码/确认词拒绝、注销后拒登）、全量 GET 路由 × 三角色无 5xx。
- 前端 `tsc --noEmit`、变更文件 ESLint 与生产构建通过。

### 规划对应与边界

对应系统设计 §9「注销账号」、§11.1 目录体系、§15 权限表、§27 运营后台，以及 `规划设计/zhanghao.md`、`buchong.md`、`yonghu.md`、`ALLIN_v1.md` 的下一批实施建议。目录维护与工作台属于 P2 运营能力；账号注销与导出是账号基础设施，商业化与 AI 仍独立关闭。

探索槽位参数（启用阈值、槽位数量与位置、候选时效与评价门槛）是**待确认的默认值**，集中定义在 `interaction/recommendations.py` 顶部，尚未在真实内容规模下评估曝光效果。

本地测试与浏览器验证不能代替 PostgreSQL、MinIO、ClamAV、Celery 的真实联调。注销保留期只落地字段与说明，不含自动清理；结构降级会丢失举报/申诉治理状态与教师/学期停用字段，不能据此宣称业务可安全回退。

### 本地验证

- 后端全量 89 项测试通过（隔离内存 SQLite）：基线 63 项 + 账号 10 项 + 运营 5 项 + 互动 11 项。
- 前端 `tsc --noEmit`、变更文件 ESLint 与生产构建通过。
- `alembic upgrade head --sql` 生成单一 head 的线性链路 `0001 → 0002 → 0003 → 0004 → 0005 → 0006 → 0007`，无分叉。
- 未执行真实 PostgreSQL 迁移与并发、对象存储、扫描与 Worker 联调。

## 0.0.1-beta.9 — 2026-10-16

将单项迭代扩大为 P2 供需、关注通知与运营处理闭环。

- 新增需求详情、同课程公开资料候选回应和状态处理。发起者/管理员可确认解决或关闭，已解决可重开，已关闭仅管理员可重开；修订号、审计与事件在同一业务事务内处理。
- 候选按需求/资料去重，不自动解决；后续资料下架或转为私有时，遮蔽 ID/标题并显示不可访问，保留处理记录。批量读取解决/候选摘要，避免新增逐条查询。
- 上传回应携带原需求入口，审核发布后回到需求提交候选；不绕过来源、扫描、审核或下载授权。
- 需求结果与新候选生成站内通知；课程/创作者发布通知合并去重。通知中心修复 `read`/`is_read` 契约，补分页、全部已读与需求直达。
- 新增管理员评论管理列表和失败事件列表/重新排队，保留正文遮蔽和角色权限；重试校验尝试次数，不把排队当作已投递。
- 需求、候选、通知、本人关注和本人需求补分页；课程需求列表实际应用课程过滤，关注缓存按账号隔离。
- 前后端版本同步，新增迁移 `0004_beta9`；保留 beta7/beta8 本地改动，本轮前快照位于 `.review/beta9-baseline`。
- 对 `规划设计` 全部 12 份文档添加实施对照；`ALLIN_v1.md` 汇总已实现、本轮完成、后置和待决策项。商业化、AI、纯图片预览/水印、生产设施与解决贡献奖励均未冒充已完成。

### 验证边界

本地 SQLite、接口测试数据、前端生产构建和离线迁移 SQL 分别证明其对应契约；不替代真实 PostgreSQL 并发、MinIO、ClamAV、Worker、邮件及部署恢复验收。回退前须停止相关写入并备份新增回应与事件；已有 `request.updated` 事件时旧类型约束会拒绝降级，不自动删记录。

### 本地验证

- 全量后端 63 项测试通过；审查后补充解决资料 ID 的前后审计记录并单独复测生命周期用例通过。
- 最终前端生产构建、TypeScript、变更文件 ESLint、新增 Python 文件 Ruff 和需求/事件操作模块 Mypy 通过。
- Chromium 6 组接口测试数据验收通过：需求分页/上传回应/候选确认、冲突恢复与失效资料、角色重开与通知已读/分页/直达、评论队列与事件失败重试、本人需求分页及纠错/需求链接、审核员权限限制；包括 390px 移动端检查。
- PostgreSQL 升级/回退 SQL 已生成；真实数据库迁移与并发仍未验证。规划目录 12 份文档均有 beta9 对照，总矩阵见 `规划设计/ALLIN_v1.md`。

## 0.0.1-beta.8 — 2026-10-16

在 beta7 本地实现基础上继续 P2 评论治理。

- 作者可撤回自己的评论，管理员可填写理由隐藏/恢复；撤回与隐藏独立保存，原文及已有正常回复保留，对外返回占位和 `body=null`。管理员不能恢复作者已撤回内容。
- 新增 `comment.moderate` 权限，仅授予管理员；普通资料审核员不自动拥有评论管理权限。active 账号才可发言或治理。
- 评论列表、发表和治理统一校验资料元数据权限，修复评论绕过私有资料限制的问题；仅支持回复正常主评论，隐藏/撤回后禁止新回复。
- 状态变更校验修订号，回复和治理共用评论行锁；事务内追加审计，不复制评论正文。同状态重试不重复计次或审计。
- 资料讨论区补齐确认弹窗、处理理由、失败重试和版本冲突后的刷新；按账号隔离列表缓存。
- 新增迁移 `0003_beta8`，前后端及锁文件同步 `0.0.1-beta.8`；保留 beta7 未提交改动，本轮前快照位于工作区 `.review/beta8-baseline`。
- 推进规划中的评论状态/权限/数据保留约束，以及求资料解决/关闭/重新打开的候选契约；后者尚未实现，处理主体与受众规则待确认。

### 验证与回退边界

本地验证结果在交付时列明。SQLite、接口测试数据和离线 PostgreSQL SQL 不代表真实 PostgreSQL 并发、迁移或扫描/存储/Worker 联调。

结构降级会移除评论治理状态，旧应用可能重新显示已遮蔽原文。回退前必须停止公开评论读写并备份治理状态，不能把 downgrade 成功等同业务安全回退。

### 本地验证

- 后端全量 56 项测试通过，包含新增 6 项 beta8 用例；使用隔离 SQLite。
- 前端生产构建、TypeScript、变更文件 ESLint、新增 Python 文件与权限模块 Ruff、治理服务与 DTO 的 Mypy 通过。
- Chromium 接口测试数据验证管理员隐藏/恢复、必填理由、连续输入焦点、失败重试、版本冲突后重开、作者撤回、既有回复保留与角色按钮限制；桌面和 390px 移动端已检查。
- 已生成 PostgreSQL 升级/回退 SQL，并在含旧数据的内存 SQLite 表上执行本次迁移的升级/回退，旧正文及默认状态检查通过；未执行真实 PostgreSQL 迁移与并发验证。

## 0.0.1-beta.7 — 2026-10-16

基于 beta6 `aafab51`，继续推进 P2「共建与质量」：课程攻略与规则推荐。

- 课程页新增攻略浏览与分享入口；`/courses/{id}/guides` 按既有 `guide` 类型展示已发布攻略，支持游标分页、空状态和加载失败提示。
- 分享入口带入课程与攻略类型，上传页补充适用学期、教师、学习路线和个人经验范围提示。复用原有来源声明、安全扫描、审核、版本、预览与下载流程。
- 新增 `GET /recommendations`，可限制课程并返回最多 20 条及推荐理由；首页与课程页展示推荐。
- 先按既有元数据权限过滤，排除未发布、待审公开版本与已过时内容，再按关注关系、现行状态、收缩评分、收藏数和更新时间排序；使用固定资源 ID 打破平局。
- 前后端及锁文件版本同步；无需新增数据库迁移。不启用商业化或 AI，不引入行为追踪。
- 同步系统设计，并在 `规划设计/ALLIN_v1.md`、`yonghu.md`、`wenjian.md`、`buchong.md`、`rongyu.md` 中补充当前实施契约、评论治理与冷启动后续设计，区分已实现与建议。

### 规划对应与边界

对应系统设计 §6、§20.2 的课程攻略与规则推荐，以及 `规划设计/ALLIN_v1.md` Phase 2。攻略以文件资料形式贡献，在线文章编辑后置；规则是本版本明确的可解释排序，不代表个性化推荐效果已经验证。

本地测试与浏览器验证结果在交付时单独列明；SQLite 和浏览器接口测试数据不能代替 PostgreSQL、MinIO、ClamAV、Celery 的真实联调。

### 本地验证

- 44 项既有后端回归通过；新增 6 项 beta7 用例最终复测全部通过。分轮验证：全量首轮唯一失败是新增测试使用了不存在的账号状态，修正为既有状态规则后复测通过。
- 前端生产构建、TypeScript、ESLint、新增 Python 文件 Ruff 与推荐模块 Mypy 检查通过。
- Chromium 接口测试数据验证 5 组场景：推荐理由、攻略入口与分页、上传预选及草稿请求、桌面/390px 布局、空结果与故障提示；截图保存在工作区 `.review/beta7`。

## 0.0.1-beta.6 — 2026-10-16

基于 beta5，落实 P2「共建与质量」：评论、贡献者主页与贡献记录。

- 资料详情页新增讨论区：评论列表（游标分页）、发表评论与一层回复，作者名可跳转贡献者主页；评论按纯文本存储，不执行 HTML。
- 新增贡献者主页 `/creators/{id}`：公开资料（昵称、头像、一句话介绍、简介）、贡献构成与贡献分，以及按作者过滤的公开资料列表；可关注/取消关注贡献者，与收藏资料分开。
- 新增 `/me/contributions`：贡献汇总、贡献记录（游标分页）与贡献者资料编辑（一句话介绍、简介、是否公开）。昵称与头像始终以账号资料为准，不重复维护。
- 关闭公开后，他人只能看到昵称与贡献次数；本人仍可读回未公开的简介原文以便编辑。
- 后端：`GET/PUT /me/creator-profile`；`GET /resources` 支持 `owner_id` 过滤（可见范围仍由服务端权限决定）；资源详情返回 `owner_id` 与 `owner_display_name`。
- 个人中心导航与账号菜单新增「我的贡献」入口；关注列表中的创作者可跳转其主页。
- 新增 `test_beta6` 3 项；前后端及锁文件版本同步。

### 规划对应与边界

对应 `规划设计/ALLIN_v1.md` 第二阶段与系统设计 §20.2、§24.1 的「评论、贡献者主页、贡献记录」。贡献分仅用于展示与运营，不承诺兑换现金；资金、结算与提现仍属 P3，本次未启用。

验证结果在 PR 中逐项说明；SQLite 与浏览器测试数据不能代替 PostgreSQL、MinIO、ClamAV、Celery 的真实联调。

## 0.0.1-beta.5 — 2026-10-15

基于 beta4 `93617f2`，完成管理员批量导入、分类纠错和个人资料编辑。

- `/admin/imports` 批量选择最多 50 个文件，每个文件创建独立资料。课程代码优先，其次名称、别名；固定正则匹配资料类型。多课程命中、类型冲突和未匹配均交给人工选择，展示匹配依据，逐项确认分类与来源。分类接口失败仍可手动填写。
- 文件沿用上传会话、安全扫描、来源声明与独立审核。逐项保存、稳定幂等键及失败重试；扫描未通过只保存草稿。页面刷新后在“我的资料”继续已保存草稿；未保存的本地队列不跨页面持久化。
- 资料详情可反馈建议课程和资料类型。管理员核对后采纳并修复分类；在同一事务内校验公开版本、资源修订号、更新分类并关闭工单，写入审计、贡献与结果通知，课程变化清空原开课实例。保留原文件与内容版本。
- 接受、拒绝、已修复均通知提交者；修复现有纠错状态更新的异步时间戳读取错误，同状态重试直接返回当前结果。
- `/me/profile` 编辑昵称、头像、可选性别、简介、学院专业与入学年份。账号菜单显示头像，加载失败使用昵称首字；不公开新增个人资料字段，不改变认证和权限。档案审计仅记变更字段名。
- 头像改为直接上传：`POST /me/avatar` 接收 PNG/JPEG/WebP（≤2 MiB），按真实字节校验后写入公开桶，地址由 `S3_PUBLIC_BASE_URL` 拼装；不再依赖外部图床地址，页面提供上传与恢复默认头像。
- 新增可回退迁移 `0002_beta5`；前后端及锁文件版本同步。

### 规划对应与边界

继续落实工作目录 `规划设计/zhanghao.md` 的用户资料与权限分离、`wenjian.md` 的人工纠错和原件保留、`design（2）.md` 的分类确认/修改/拒绝与核心流程不依赖 AI、`yonghu.md`/`buchong.md` 的反馈和通知闭环。既有重写设计文档保留为业务状态与接口契约；不在本次启用 AI、商业化、行为追踪或复杂推荐。

验证结果在 PR 中逐项说明；SQLite、浏览器测试数据和离线迁移 SQL 均不能代替 PostgreSQL、MinIO、ClamAV、Celery 的真实联调。

## 0.0.1-beta.4 — 2026-10-15

基于 beta3 的 `750a077` 重构核心操作逻辑，优先减少重复输入、无效跳转和同时需要理解的信息；三步按用户完成目标的操作阶段验收，输入、登录和必要检查不机械算作一次点击。

- 首页提供收藏、关注、上传和草稿/审核进度入口；桌面主导航提供求资料，移动端保留搜索与课程入口并新增上传。
- 课程页先展示分类资料，课程介绍、培养方案和开课范围收纳到可展开区域；上传与求资料自动带入课程，需求表单直接展开。
- 资料详情前置预览、下载与收藏；游客有明确的登录后下载入口，登录回跳保留课程参数。
- 上传整合为「选择课程与文件 → 补充信息与来源 → 提交审核」；文件名预填标题，可选说明折叠。提交自动保存草稿，仍可独立暂存。
- 现有草稿修改后也可直接提交，先保存成功再送审；提交失败保留同一草稿及最新修订号供重试。
- 搜索页无关键词时可浏览资料，类型筛选立即生效，浏览器前进/返回同步表单；空结果可直接提出需求。
- 保留来源声明、安全检查、服务端权限与审核流程，不将跳过校验作为减少步骤的方法。

| 目标 | beta4 常用路径 |
| --- | --- |
| 找到并阅读资料 | 搜索 → 资料详情 → 预览/下载 |
| 从课程贡献资料 | 上传该课程资料 → 选择文件、补充信息 → 提交审核 |
| 从课程提出需求 | 为这门课求资料 → 填写描述 → 发布需求 |
| 继续草稿 | 我的资料 → 查看与管理并修改 → 提交审核 |
| 继续阅读收藏 | 我的收藏 → 资料详情 → 预览/下载 |

### 验证边界

生产构建、TypeScript 与变更文件 ESLint 检查通过。浏览器使用明确的 API/存储测试数据，验证核心路径、登录回跳、失败重试、安全检查门槛与移动端布局；这不代表 PostgreSQL、MinIO、ClamAV 或 Worker 真实联调。beta3 历史记录与验证边界保留。

## 0.0.1-beta.3 接续 — 2026-10-15

完成 beta3「接续工作」中的界面与接口部分；Docker 真实联调仍未完成（见验证边界）。

### 新增

- `GET /admin/corrections`：管理端纠错工单列表（状态过滤、游标分页），处理继续使用既有 `POST /admin/corrections/{id}/status`。
- 密码找回确认页 `/reset-password`：与邮件中的重置链接对应，支持手动粘贴令牌、重新申请，完成后从 URL 清理令牌。
- 投诉管理界面 `/admin/reports`：按状态筛选、处理记录时间线，按服务端允许的流转追加事件；临时限制/解除访问与最终认定分开记录。
- 纠错管理界面 `/admin/corrections`：待处理/全部筛选，处理结论（接受/拒绝/已修复）与可选修复版本。
- 求资料页 `/requests`：需求列表、按课程搜索新建、幂等关注；课程页新增关注按钮与「为这门课求资料」入口，个人中心新增「我的关注」。
- 资料详情页「举报或纠错」弹窗补齐纠错提交（绑定当前公开版本），与原有举报工单分开。

### 验证边界

- 后端单元/集成测试（内存 SQLite）通过；不覆盖 PostgreSQL 并发、MinIO/ClamAV/Worker 联调。
- 前端完成类型检查与生产构建；未做浏览器端到端验证。
- Docker 环境下的上传—扫描—预览—审核—下载联调、重启恢复与配额并发验证仍待进行（本机无 Docker）。

## 0.0.1-beta.3 — 2026-10-15

基于 beta2 的 `6cb7188` 继续迭代，保留原有前台、专题模块和共建记录。

### 修复

- 详情、列表、文件元数据和搜索应用元数据权限；列表在分页前过滤。
- 下载重试复用业务授权并重新检查权限、状态和期限，不缓存已过期的签名链接。
- 图片缩略图可以作为预览，PDF 优先使用受限 PDF；尊重作者关闭预览的选择。
- 已完成的扫描拒绝和转换失败与任务失败状态一起保存，意外异常仍回滚。
- 来源校验按协议、主机和端口精确比较；通用幂等记录验证所属账号。
- 审核员只能检查对应审核工作范围，不能读取未提交的他人草稿或改写作者内容。
- 上传只指定一个主文件，其余为附件；重复保存更新原草稿，修改后必须保存再提交。
- 容器内 API 地址与浏览器可达的存储签名地址分开配置；开发 Worker 使用转换器镜像，默认启动扫描器。
- 普通已激活账号可以投稿，无需手工提升成贡献者；待验证和受限账号不能提交或上传。

### 新增与拆分

- `access/visibility.py`：列表和搜索复用的数据库可见性规则。
- `content/management.py`、`management_router.py`：作者工作区、版本历史、精确版本快照。
- 我的资料支持分页、草稿管理、审核意见、修订、文件替换与撤回；原纠错/求资料记录保留为独立组件。
- 审核入口指向待审版本；原件检查使用专用授权，不等同于公开下载或贡献计次。
- 邮箱验证与重新申请页面；验证链接不再落入不存在的页面。
- 后端回归测试、隔离数据库/API 测试、浏览器接口契约测试和基础 CI 工作流。
- API 与前端包版本统一为 `0.0.1-beta.3`；设计文档原版本 `0.0.1bate2` 保留。

### 验证边界

数据库测试采用内存 SQLite，测试中仅对 JSONB 做编译适配；不代表 PostgreSQL 并发与迁移验证。
浏览器测试使用明确的测试接口数据，检查作者编辑提交、指定版本审核和移动端布局；不代表对象存储、扫描器、Celery 的实际联调。
持续检查工作流已配置，是否在线通过须以实际 GitHub Actions 运行记录为准。

### 接续工作

在有 Docker 的环境完成 PostgreSQL、MinIO、ClamAV、Worker 的真实上传—扫描—预览—审核—下载联调，以及重启恢复和配额并发验证。
后续再推进投诉/纠错管理界面、密码找回页面、资料需求与关注体验；商业化和 AI 继续保持独立开关与后置范围。
