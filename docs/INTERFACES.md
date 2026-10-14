# 跨模块接口声明

只有本文件列出的函数允许被其他模块调用。签名变更必须同步修改本文件与所有调用点。
调用方式：函数体内局部导入，例如 `from app.modules.access.service import authorize`。

## identity

```python
async def get_user(db: AsyncSession, user_id: uuid.UUID) -> User            # 找不到抛 NotFound
async def get_users(db: AsyncSession, user_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, User]
def display_name_of(user: User) -> str                                      # 匿名化后返回"已注销用户"
async def revoke_all_sessions(db: AsyncSession, user_id: uuid.UUID, *, reason: str) -> int
async def is_school_verified(db: AsyncSession, user_id: uuid.UUID) -> bool
```

## catalog

```python
async def get_course(db: AsyncSession, course_id: uuid.UUID) -> Course       # 找不到抛 NotFound
async def get_courses(db: AsyncSession, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, Course]
async def get_offering(db: AsyncSession, offering_id: uuid.UUID) -> CourseOffering
async def course_brief(db: AsyncSession, course_id: uuid.UUID) -> CourseBrief
# CourseBrief: course_id, code, name, college_name, aliases: list[str]
```

## content

```python
async def get_resource(db: AsyncSession, resource_id: uuid.UUID) -> Resource
async def get_version(db: AsyncSession, version_id: uuid.UUID) -> ResourceVersion
async def get_file(db: AsyncSession, file_id: uuid.UUID) -> ResourceFile
async def published_version(db: AsyncSession, resource: Resource) -> ResourceVersion | None
async def version_files(db: AsyncSession, version_id: uuid.UUID) -> list[tuple[VersionFile, ResourceFile]]
async def resource_course_id(db: AsyncSession, resource_id: uuid.UUID) -> uuid.UUID
async def assert_visible(db: AsyncSession, resource: Resource) -> None
# 可见性唯一入口：未发布（unpublished/withdrawn/archived）一律抛 NotFound。
```

`content` 拥有资源的 `publication_status`、`freshness_status`、`published_version_id`、`revision`。
其他模块只能通过以下函数改变这些列：

```python
async def mark_published(db: AsyncSession, *, resource_id, version_id, expected_revision, actor_id, request_id) -> None
async def mark_withdrawn(db: AsyncSession, *, resource_id, expected_revision, actor_id, reason, request_id) -> None
async def set_freshness(db: AsyncSession, *, resource_id, status, actor_id) -> None
```

## access

```python
async def authorize(
    db: AsyncSession, *,
    user: User | None,
    resource_id: uuid.UUID,
    version_id: uuid.UUID,
    file_id: uuid.UUID | None = None,
    capability: Capability,
) -> None
# 按 §15.1 判定顺序执行；不通过时抛 Forbidden 或 NotFound，不返回布尔值。
# 这是唯一允许判定"能否读/预览/下载"的函数，模块不得自行实现权限判断。

async def issue_preview_grant(db: AsyncSession, *, user: User | None, resource_id, version_id, request_id) -> PreviewGrantResult
async def issue_download_grant(
    db: AsyncSession, *, user: User, resource_id, version_id, file_id,
    idempotency_key: str, request_id: str,
) -> DownloadGrantResult
# DownloadGrantResult: grant_id, download_url, url_expires_at, file_name, version_no
```

## moderation

```python
async def open_review(db: AsyncSession, *, version_id: uuid.UUID, submitter_id: uuid.UUID) -> Review
async def get_review(db: AsyncSession, review_id: uuid.UUID) -> Review
async def decide(
    db: AsyncSession, *, review_id: uuid.UUID, reviewer: User,
    decision: ModerationDecisionType, reason: str, snapshot_revision: int, request_id: str,
) -> ModerationDecision
# 决定不可原地覆盖；并发时过期快照抛 Conflict（§13.3、§29）。
async def can_review(db: AsyncSession, reviewer: User, version_id: uuid.UUID) -> bool
```

## interaction

```python
async def resource_rating_summary(db: AsyncSession, resource_id: uuid.UUID) -> RatingSummary
# RatingSummary: average: float, count: int, shrunk: float   （§20.1 小样本采用收缩评分）
async def has_access_history(db: AsyncSession, *, user_id: uuid.UUID, resource_id: uuid.UUID) -> bool
# 评价资格：至少获得过该内容访问权（§20.1）。
```

## notification

```python
async def notify(
    db: AsyncSession, *, user_id: uuid.UUID, type: NotificationType,
    title: str, body: str, data: dict | None = None,
) -> None
async def dispatch_event(db: AsyncSession, event_id: uuid.UUID) -> None
# 消费 domain_events 并转为通知；通知失败不回滚已成功的事务（§21）。
async def fanout_due_events(db: AsyncSession, limit: int = 200) -> int
```

## jobs

```python
async def enqueue(
    db: AsyncSession, *, job_type: JobType, target_id: uuid.UUID | str,
    input_version: int = 1, payload: dict | None = None, dedupe_key: str | None = None,
) -> Job
# 幂等约束：目标 + 输入版本 + 处理器版本 + 任务类型（§22.2）。
async def run_job(job_id: str) -> str          # Celery 任务入口，返回 JobStatus 值
async def dispatch_due_jobs() -> dict          # 扫描器：到期任务与超时租约
```

## creator

```python
async def record_contribution(
    db: AsyncSession, *, user_id: uuid.UUID, kind: ContributionKind,
    reference_type: str, reference_id: uuid.UUID, note: str | None = None,
) -> None
# 有效贡献 ≠ 授权链接次数（§24.1）。自下载、重复刷取不得计入。
async def contribution_summary(db: AsyncSession, user_id: uuid.UUID) -> ContributionSummary
```

## commerce

```python
async def entitlement_grants(db: AsyncSession, *, user_id: uuid.UUID, scope_type: ScopeType, scope_id: uuid.UUID) -> list[Entitlement]
# 供 access 模块在判定权益时调用；未启用时返回空列表，不伪造权益。
async def confirm_payment(db: AsyncSession, *, channel: str, payload: dict) -> PaymentAttempt
# 只接受验签通过的通知；重复回调不重复授予权益（§24.3）。
```

## ai

```python
async def request_process(
    db: AsyncSession, *, resource_id: uuid.UUID, version_id: uuid.UUID,
    kind: AIProcessKind, requested_by: uuid.UUID | None,
) -> AIProcess
# AI_ENABLED=false 时抛 NotEnabled，不返回假结果（§23、§25.2）。
async def process_result(db: AsyncSession, process_id: uuid.UUID) -> AIProcess
```
