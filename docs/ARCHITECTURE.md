# 实现契约（0.0.1bate2）

本文件是《南邮课程知识平台_系统设计_0.0.1bate2》的**代码级落地契约**。
设计文档管业务规则，本文件管文件位置、命名、跨模块接口与不可协商的约定。
发生冲突时以设计文档为准，并回头修改本文件。

## 1. 目录结构与文件所有权

```
backend/
  alembic/                     [冻结] 迁移环境
  alembic/versions/            [冻结] 迁移版本
  app/
    main.py                    [冻结] 应用装配
    config.py                  [冻结] 配置
    db.py                      [冻结] 引擎/会话/Base/混入
    models_registry.py         [冻结] 全部模型注册（Alembic 与建表依赖它）
    core/                      [冻结] 跨模块基础层
    modules/<name>/            [模块自有] 见下表
      __init__.py
      models.py                表定义（必须导出 __all__）
      schemas.py               Pydantic DTO
      service.py               应用服务（业务规则唯一落点）
      router.py                必须导出名为 `router` 的 APIRouter
    workers/                   [冻结] celery_app；tasks.py 由 jobs 模块提供
  scripts/                     [模块自有] 种子与运维脚本
  tests/                       [测试自有]
frontend/                      [前端自有]
docs/                          [冻结]
docker-compose.yml             [冻结]
docker-compose.prod.yml        [冻结]
nginx/nginx.conf               [冻结]
```

模块与所有者：

| 模块 | 目录 | 路由前缀 |
| --- | --- | --- |
| identity | `app/modules/identity` | `/auth`、`/me` |
| catalog | `app/modules/catalog` | `/courses`（含学院/专业/培养方案/开课实例） |
| content | `app/modules/content` | `/resources`、`/uploads` |
| moderation | `app/modules/moderation` | `/reports`、`/admin/reviews`、`/admin/audit` |
| access | `app/modules/access` | `/resources/{id}/preview-grants`、`/resources/{id}/download-grants`、`/admin/entitlements` |
| interaction | `app/modules/interaction` | `/me/favorites`、`/resources/{id}/versions/{vid}/rating`、`/me/follows`、`/requests`、`/corrections`、`/search` |
| notification | `app/modules/notification` | `/notifications` |
| jobs | `app/modules/jobs` | `/admin/jobs` |
| commerce | `app/modules/commerce` | `/orders`、`/admin/refunds`、`/admin/reconciliations` |
| creator | `app/modules/creator` | `/creators`、`/me/contributions`、`/me/ledger`、`/me/withdrawals` |
| ai | `app/modules/ai` | `/ai` |

`main.py` 按固定顺序 `from app.modules.<name>.router import router as <name>_router` 并 `include_router(router, prefix="/api/v1")`。
**模块不得自行决定前缀，不得修改 main.py。**

## 2. 跨模块协作规则

1. 模块只能导入 `app.core.*`、`app.db`、`app.config`。
2. 跨模块引用**表**用字符串外键，不导入对方模型类：
   `mapped_column(ForeignKey("users.id"), index=True)`。这样 Alembic 与建表只需 `models_registry.py` 汇总导入。
3. 跨模块调用**服务**必须在函数体内局部导入，禁止模块顶层互相导入（防循环）：
   ```python
   def submit_version(...):
       from app.modules.moderation.service import enqueue_review
   ```
4. 跨模块的稳定接口在 `docs/INTERFACES.md` 中声明，签名变更必须同步更新。
5. 不允许任何模块直接改写其他模块拥有的关键状态列；只能调用对方服务函数。

## 3. 表名契约（跨模块外键只允许引用这些表）

`users`、`user_profiles`、`user_identities`、`sessions`、`auth_tokens`
`universities`、`colleges`、`majors`、`curricula`、`courses`、`curriculum_courses`、`semesters`、`teachers`、`course_offerings`、`offering_teachers`、`course_aliases`
`resources`、`resource_versions`、`resource_files`、`version_files`、`file_derivatives`、`rights_declarations`、`tags`、`resource_tags`、`resource_relations`、`upload_sessions`
`reviews`、`moderation_decisions`、`reports`、`report_events`、`audit_logs`
`access_policies`、`entitlements`、`download_grants`、`download_quota_usage`、`preview_grants`
`favorites`、`ratings`、`follows`、`comments`、`corrections`、`resource_requests`、`resource_request_follows`
`notifications`、`domain_events`
`jobs`
`products`、`prices`、`orders`、`order_items`、`payment_attempts`、`refunds`、`settlements`、`ledger_entries`、`withdrawals`
`contributions`、`creator_profiles`
`ai_processes`、`ai_budgets`、`ai_usages`

## 4. 通用约定（设计文档 §10、§25.1）

- 主键 UUID4，API 传字符串。前端类型一律 `string`。
- 时间 `TIMESTAMP WITH TIME ZONE`，存 UTC，序列化 ISO 8601。
- 金额用 `Numeric(18, 4)` 定点，禁用 float。文件大小用整数字节。
- 状态用 `app.core.enums` 里的枚举（`String` 存值 + `CheckConstraint`）。
- 每个业务表有 `created_at`、`updated_at`；可并发编辑的加 `revision`（整数，条件更新用）。
- 对外响应一律用 DTO，不返回 ORM 对象、`storage_key`、密钥、内部风险参数。
- 列表统一 `{"items": [...], "next_cursor": str|null, "has_more": bool}`，默认 20、上限 100。
- 成功 200/201/202/204；输入不符 422；状态或幂等冲突 409；超限 429。
- 未认证 401；已认证无权限 403；私有对象统一 404 避免泄露存在性。
- 错误体固定：
  ```json
  {"error": {"code": "RESOURCE_NOT_DOWNLOADABLE", "message": "…", "request_id": "…", "details": {}}}
  ```
  由 `app.core.errors` 的异常处理器统一产生，业务代码只抛 `AppError` 子类。
- 影响额度或关键状态的 POST 接受 `Idempotency-Key` 头，用 `app.core.idempotency`。
- 文件内容只走上传会话，不向接收 JSON 的接口混发 multipart。

## 5. 权限码（设计文档 §15）

`app/core/permissions.py` 定义唯一映射，业务代码只能引用常量：

```
resource.submit          resource.revise          resource.withdraw
resource.review          resource.publish         report.handle
correction.handle        catalog.manage           user.suspend
entitlement.grant        audit.view               job.retry
finance.view             settlement.manage        ai.invoke
```

角色：`guest`、`user`、`contributor`、`reviewer`、`admin`。
判定顺序（§15.1）固定为：账号状态 → 对象存在与发布状态 → 文件安全状态 → 来源允许的分发范围 → 访问策略 → 有效权益 → 配额与风险限制。
未知策略默认拒绝。作者与管理员权限不能绕过文件隔离与未授权商业传播。

## 6. 冻结的共享工具

| 文件 | 提供 |
| --- | --- |
| `app/core/enums.py` | 全部状态枚举与取值 |
| `app/core/errors.py` | `AppError` 及子类、`install_error_handlers(app)` |
| `app/core/pagination.py` | `CursorPage`、`encode_cursor`/`decode_cursor` |
| `app/core/permissions.py` | 权限码、角色映射、`require(*codes)` 依赖 |
| `app/core/security.py` | Argon2id 密码、随机会话令牌、令牌摘要 |
| `app/core/storage.py` | `ObjectStorage`（S3 兼容）、预签名上传/下载 URL |
| `app/core/idempotency.py` | `Idempotency-Key` 记录与冲突判定 |
| `app/core/audit.py` | `record_audit(...)` |
| `app/core/deps.py` | `get_db`、`get_current_user`、`get_optional_user`、`get_request_id` |

## 7. 前端契约

- 令牌走 `HttpOnly + Secure + SameSite` Cookie，**不再使用 localStorage**。
- 修改请求带 CSRF 双提交令牌（`X-CSRF-Token`），由 `/api/v1/auth/csrf` 下发。
- 设计令牌层：`frontend/src/styles/tokens.css` 引入 DNDL `tokens.css` 的值并映射为 Tailwind v4 `@theme` 语义变量；组件与页面只用语义变量，不硬编码品牌色。
- 默认直角（`--dn-radius: 0`）；保留大面积品牌实色色块；文字配色用 `--dn-text-on-color` / `--dn-text-secondary`。
- 禁止假数据：无数据时给明确静态回退，不编造用户数、进度、更新时间或实时状态。
- 设计文档 §26.2 的 D15（最终视觉方向与字体）与本项目采用的 DNDL 存在待确认冲突，见 `docs/DECISIONS.md`。

## 8. 版本与交付口径（设计文档 §40）

分别报告：源代码已实现 / 静态检查通过 / 实际流程通过 / 已部署 / 线上验证通过。不得互相替代。
