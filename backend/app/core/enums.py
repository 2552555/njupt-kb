"""受约束状态枚举。

设计文档 §13：处理、审核、发布与时效是独立状态维度，不混用一个字段。
所有状态列以 String 存储枚举值，并在迁移中加 CheckConstraint。
"""

from __future__ import annotations

from enum import StrEnum


class AccountStatus(StrEnum):
    """§14.1 账号状态。"""

    PENDING_VERIFICATION = "pending_verification"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    BANNED = "banned"
    DELETED = "deleted"


class IdentityKind(StrEnum):
    """§14.1 身份声明来源。邮箱验证只证明当时控制该邮箱，不证明在读身份。"""

    EMAIL = "email"
    SCHOOL = "school"


class IdentityStatus(StrEnum):
    PENDING = "pending"
    VERIFIED = "verified"
    REVOKED = "revoked"
    EXPIRED = "expired"


class AuthTokenPurpose(StrEnum):
    """§14.2 找回密码与邮箱验证使用有期限、可撤销、一次性令牌。"""

    EMAIL_VERIFICATION = "email_verification"
    PASSWORD_RESET = "password_reset"


class Role(StrEnum):
    """§15 角色。权限映射见 app/core/permissions.py。"""

    USER = "user"
    CONTRIBUTOR = "contributor"
    REVIEWER = "reviewer"
    ADMIN = "admin"


class UploadSessionStatus(StrEnum):
    """§13.1 上传会话。"""

    INITIATED = "initiated"
    UPLOADING = "uploading"
    RECEIVED = "received"
    VERIFIED = "verified"
    EXPIRED = "expired"
    ABORTED = "aborted"


class ScanStatus(StrEnum):
    """§13.1 ResourceFile.scan_status。

    rejected 表示检测拒绝；failed 表示检查未能完成。扫描服务不可用时留在隔离区等待，不默认 passed。
    """

    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    REJECTED = "rejected"
    FAILED = "failed"


class DerivativeStatus(StrEnum):
    """§13.1 FileDerivative.status。转换失败不改变原件审核事实。"""

    PENDING = "pending"
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"


class JobStatus(StrEnum):
    """§13.1 / §22 后台任务。"""

    QUEUED = "queued"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobType(StrEnum):
    """§22.2：原件安全扫描与内容处理属于不同任务类别，不共用一个"处理成功"标志。"""

    FILE_SCAN = "file.scan"
    FILE_DERIVE = "file.derive"
    NOTIFICATION_DISPATCH = "notification.dispatch"
    EVENT_FANOUT = "event.fanout"
    AI_PROCESS = "ai.process"


class FailureClass(StrEnum):
    """§22.2：重试只针对临时故障；永久结果不无限重试。"""

    TRANSIENT = "transient"
    PERMANENT = "permanent"
    UNKNOWN = "unknown"


class ReviewStatus(StrEnum):
    """§13.2 版本审核。

    beta15 按《用户权责、内容治理与授权收益体系设计文档》§14.1 增加 `NEEDS_INFO`（补证）：
    补证与重投创建关联修订，不覆盖旧审核结论。
    """

    DRAFT = "draft"
    PENDING = "pending"
    NEEDS_INFO = "needs_info"
    APPROVED = "approved"
    REJECTED = "rejected"


class PublicationStatus(StrEnum):
    """§13.2 资源发布。仅通过版本可以公开；撤下可经授权恢复。"""

    UNPUBLISHED = "unpublished"
    PUBLISHED = "published"
    WITHDRAWN = "withdrawn"
    ARCHIVED = "archived"


class FreshnessStatus(StrEnum):
    """§13.2 内容时效，作为适用性标签，不自动导致删除。"""

    CURRENT = "current"
    POSSIBLY_OUTDATED = "possibly_outdated"
    OUTDATED = "outdated"


class ModerationDecisionType(StrEnum):
    """§13.2 审核决定。beta15 按用户权责体系 §14.1、§16.3 增加「补证」。"""

    APPROVE = "approve"
    NEEDS_INFO = "needs_info"
    REJECT = "reject"


class ReportStatus(StrEnum):
    """§18 投诉工单。"""

    OPEN = "open"
    TRIAGED = "triaged"
    INVESTIGATING = "investigating"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"
    APPEALED = "appealed"


class CorrectionStatus(StrEnum):
    """§20.2 纠错工单。"""

    OPEN = "open"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    FIXED = "fixed"


class RequestStatus(StrEnum):
    """§20.2 求资料。"""

    OPEN = "open"
    FULFILLED = "fulfilled"
    CLOSED = "closed"


class ScopeType(StrEnum):
    """§15.1 AccessPolicy 范围，未知策略默认拒绝。"""

    PUBLIC = "public"
    AUTHENTICATED = "authenticated"
    VERIFIED_SCHOOL = "verified_school"
    EXPLICIT_GRANT = "explicit_grant"
    PRIVATE = "private"


class Capability(StrEnum):
    """§15 / §17.1 三类访问能力。"""

    READ_METADATA = "read_metadata"
    PREVIEW = "preview"
    READ_FULL = "read_full"
    DOWNLOAD_ORIGINAL = "download_original"


class EntitlementSource(StrEnum):
    """§15 权益来源。P3 增加订单与订阅。"""

    FREE_OPEN = "free_open"
    AUTHOR = "author"
    ADMIN_GRANT = "admin_grant"
    ORDER = "order"
    SUBSCRIPTION = "subscription"


class GrantStatus(StrEnum):
    """§17.2 DownloadGrant.status。"""

    ISSUED = "issued"
    CONSUMED = "consumed"
    EXPIRED = "expired"
    REVOKED = "revoked"


class ResourceType(StrEnum):
    """§3 内容类别：类别表示内容性质，不表示允许传播或收费。"""

    NOTE = "note"
    REVIEW = "review"
    EXAM = "exam"
    LAB_GUIDE = "lab_guide"
    CODE = "code"
    SLIDES = "slides"
    GUIDE = "guide"
    OTHER = "other"


class FilePurpose(StrEnum):
    """§12 purpose 区分主文件、附件和示例。"""

    PRIMARY = "primary"
    ATTACHMENT = "attachment"
    SAMPLE = "sample"


class DerivativeKind(StrEnum):
    """预览是 FileDerivative，不是另一个人工上传的内容版本（§12）。"""

    PREVIEW_PDF = "preview_pdf"
    THUMBNAIL = "thumbnail"
    TEXT_EXTRACT = "text_extract"
    WATERMARKED_PREVIEW = "watermarked_preview"


class RightsBasis(StrEnum):
    """§18 来源声明分开记录。"""

    ORIGINAL = "original"
    AUTHORIZED = "authorized"
    OPEN_LICENSE = "open_license"
    THIRD_PARTY = "third_party"
    UNVERIFIED = "unverified"


class CourseStatus(StrEnum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    MERGED = "merged"
    PENDING_CLASSIFICATION = "pending_classification"
    # beta10：管理员软停用课程。停用后不再进入前台选择器与检索，
    # 但已关联资料仍按既有权限读取（不物理删除、不连带撤下）。
    INACTIVE = "inactive"


class CatalogEntityStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    MERGED = "merged"


class SemesterSeason(StrEnum):
    """§11.1 统一学年与春秋学期表示。"""

    SPRING = "spring"
    FALL = "fall"
    SUMMER = "summer"


class RequirementType(StrEnum):
    REQUIRED = "required"
    ELECTIVE = "elective"
    GENERAL = "general"
    PRACTICE = "practice"


class TeacherRole(StrEnum):
    PRIMARY = "primary"
    CO_TEACHER = "co_teacher"
    ASSISTANT = "assistant"


class RelationType(StrEnum):
    """§11.2 课程替代关系。可替代不等于同一门课。"""

    REPLACES = "replaces"
    PREREQUISITE = "prerequisite"
    RELATED = "related"
    CONTINUATION = "continuation"


class NotificationType(StrEnum):
    """§21 P1 仅需可靠站内通知。"""

    SUBMISSION_RECEIVED = "submission_received"
    REVIEW_RESULT = "review_result"
    VERSION_PUBLISHED = "version_published"
    RESOURCE_WITHDRAWN = "resource_withdrawn"
    REPORT_RESULT = "report_result"
    CORRECTION_RESULT = "correction_result"
    REQUEST_UPDATED = "request_updated"
    # --- beta15：用户权责 §4.4 ---
    REVIEW_NEEDS_INFO = "review_needs_info"
    CONTRIBUTOR_APPLICATION_RESULT = "contributor_application_result"
    ASSIGNMENT_CHANGED = "assignment_changed"
    CASE_RESULT = "case_result"
    RIGHTS_NOTICE_RESULT = "rights_notice_result"
    AGREEMENT_UPDATED = "agreement_updated"
    PRICE_UPDATED = "price_updated"


class AIProcessStatus(StrEnum):
    """§13.2 AI 处理，不作为基础安全检查结果。"""

    NOT_REQUESTED = "not_requested"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class AIProcessKind(StrEnum):
    """§23 摘要、标签、知识点、辅助分类、风险提示与课程问答。"""

    SUMMARY = "summary"
    TAGS = "tags"
    KNOWLEDGE_POINTS = "knowledge_points"
    CLASSIFY = "classify"
    RISK_HINT = "risk_hint"
    QA = "qa"


class OrderStatus(StrEnum):
    """§24.2 订单状态。"""

    PENDING = "pending"
    PAID = "paid"
    CANCELLED = "cancelled"
    REFUNDING = "refunding"
    REFUNDED = "refunded"
    EXPIRED = "expired"


class PaymentAttemptStatus(StrEnum):
    """§24.3：外部支付与本地数据库不构成原子事务，必须允许"渠道已成功、本地待确认"。"""

    CREATED = "created"
    PENDING_CONFIRMATION = "pending_confirmation"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CLOSED = "closed"


class RefundStatus(StrEnum):
    """§24.3：退款未确认前记录处理中。"""

    REQUESTED = "requested"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class LedgerAccount(StrEnum):
    """§24.3：账本至少区分平台、用户或创作者账户。"""

    PLATFORM = "platform"
    CREATOR = "creator"
    USER = "user"


class LedgerDirection(StrEnum):
    DEBIT = "debit"
    CREDIT = "credit"


class SettlementStatus(StrEnum):
    """§24.3：待结算、冻结、可提现与已提现分开。"""

    PENDING = "pending"
    FROZEN = "frozen"
    WITHDRAWABLE = "withdrawable"
    WITHDRAWN = "withdrawn"
    REVERSED = "reversed"


class WithdrawalStatus(StrEnum):
    REQUESTED = "requested"
    APPROVED = "approved"
    PROCESSING = "processing"
    PAID = "paid"
    REJECTED = "rejected"
    FAILED = "failed"


class ContributionKind(StrEnum):
    """§24.1 贡献记录。贡献分用于展示与运营，不承诺兑换现金。"""

    PUBLISHED_RESOURCE = "published_resource"
    ACCEPTED_REVISION = "accepted_revision"
    ADOPTED_CORRECTION = "adopted_correction"
    RESOLVED_REQUEST = "resolved_request"


class ProductKind(StrEnum):
    """§24.2 产品可以是会员、课程包或单资源权益。"""

    MEMBERSHIP = "membership"
    COURSE_PACKAGE = "course_package"
    SINGLE_RESOURCE = "single_resource"


class DomainEventType(StrEnum):
    """§21 业务事务同时写入待处理事件记录，后台将其转换为通知。"""

    RESOURCE_VERSION_SUBMITTED = "resource.version.submitted"
    RESOURCE_VERSION_APPROVED = "resource.version.approved"
    RESOURCE_VERSION_REJECTED = "resource.version.rejected"
    RESOURCE_PUBLISHED = "resource.published"
    RESOURCE_WITHDRAWN = "resource.withdrawn"
    RESOURCE_UPLOAD_RECEIVED = "resource.upload.received"
    REPORT_CREATED = "report.created"
    REPORT_RESOLVED = "report.resolved"
    CORRECTION_CREATED = "correction.created"
    CORRECTION_RESOLVED = "correction.resolved"
    REQUEST_CREATED = "request.created"
    REQUEST_FULFILLED = "request.fulfilled"
    REQUEST_UPDATED = "request.updated"
    FILE_SCAN_FINISHED = "file.scan.finished"
    FILE_DERIVATIVE_READY = "file.derivative.ready"
    ORDER_PAID = "order.paid"
    REFUND_CONFIRMED = "refund.confirmed"
    # --- beta15：用户权责 §4.4 页面与通知 ---
    REVIEW_NEEDS_INFO = "resource.version.needs_info"
    CONTRIBUTOR_APPLICATION_SUBMITTED = "contributor.application.submitted"
    CONTRIBUTOR_APPLICATION_DECIDED = "contributor.application.decided"
    ASSIGNMENT_CHANGED = "assignment.changed"
    CASE_CREATED = "case.created"
    CASE_UPDATED = "case.updated"
    CASE_DECIDED = "case.decided"
    RIGHTS_NOTICE_RECEIVED = "rights_notice.received"
    RIGHTS_NOTICE_COUNTER = "rights_notice.counter"
    RIGHTS_NOTICE_DECIDED = "rights_notice.decided"
    # --- beta16：授权协议与双向定价的域事件（补齐 beta15 只落库不通知的边界）---
    AGREEMENT_ACCEPTED = "agreement.accepted"
    PRICE_PROPOSED = "price.proposed"
    PRICE_CONFIRMED = "price.confirmed"
    PRICE_REJECTED = "price.rejected"


# =====================================================================
# beta15：《课程资料平台用户权责、内容治理与授权收益体系设计文档》v1.0
# 与《高并发 高可用与性能工程专项设计方案》v1.0 所需的状态枚举。
# 一律以字符串存储并在迁移中加 CheckConstraint，取值以本文件为唯一来源。
# =====================================================================


class AdminPermissionScope(StrEnum):
    """`admin_permission_assignments.scope`：系统管理员内部权限的作用范围（§3.1、§15.2）。

    系统管理员不因登录后台自动获得全部敏感操作权限；按职责授予有限权限集合。
    """

    GLOBAL = "global"
    CATALOG = "catalog"
    RESOURCE = "resource"
    FINANCE = "finance"


class AssignmentCapability(StrEnum):
    """`resource_assignments.capability`：资料管理员被授予的对象级能力（§3.3）。

    RBAC 决定「能做哪类动作」，对象归属与分配决定「可对哪一个对象做」。
    """

    MAINTAIN = "maintain"
    REVIEW = "review"
    MANAGE = "manage"


class ContributorOnboardingStatus(StrEnum):
    """贡献者入驻状态机（§3.4、§14.1）。降级不抹去已生效授权与已得收益。"""

    APPLIED = "applied"
    UNDER_REVIEW = "under_review"
    ACTIVE = "active"
    REJECTED = "rejected"
    SUSPENDED = "suspended"
    EXITED = "exited"


class CaseType(StrEnum):
    """案件类型（§13.1）。三类案件分别管理，结论互不自动替代。"""

    DELIVERY_DISPUTE = "delivery_dispute"
    REVENUE_DISPUTE = "revenue_dispute"
    RIGHTS_NOTICE = "rights_notice"
    GENERAL_APPEAL = "general_appeal"


class CaseStatus(StrEnum):
    """案件状态机（§14.1）。反通知、期限与措施是案件事件，不用单一状态丢掉过程。"""

    OPEN = "open"
    TRIAGED = "triaged"
    INVESTIGATING = "investigating"
    DECIDED = "decided"
    CLOSED = "closed"


class CaseSubjectType(StrEnum):
    """案件指向的对象类型；逐对象作用域过滤，不用任意多态字符串。"""

    RESOURCE = "resource"
    RESOURCE_VERSION = "resource_version"
    COMMENT = "comment"
    ORDER = "order"
    ORDER_ITEM = "order_item"
    OTHER = "other"


class CaseEventType(StrEnum):
    """案件事件（§15.5 case_events 追加写，保留转送与送达事实）。"""

    SUBMITTED = "submitted"
    TRIAGED = "triaged"
    FORWARDED = "forwarded"
    COUNTER_NOTICE = "counter_notice"
    MEASURE_TAKEN = "measure_taken"
    MEASURE_RELEASED = "measure_released"
    DECIDED = "decided"
    CLOSED = "closed"
    NOTE = "note"


class CaseMeasureKind(StrEnum):
    """必要措施类型（§13.3、§13.4）。措施可解除，记录影响范围。"""

    UNPUBLISH = "unpublish"
    BLOCK_DOWNLOAD = "block_download"
    BLOCK_SALES = "block_sales"
    RESTRICT_PREVIEW = "restrict_preview"
    FREEZE_REVENUE = "freeze_revenue"


class CaseMeasureStatus(StrEnum):
    ACTIVE = "active"
    RELEASED = "released"


class RightsNoticeStatus(StrEnum):
    """侵权通知处理进度（§13.3）。期限按适用法定路径记录，不统一写死十五日。"""

    RECEIVED = "received"
    NEEDS_INFO = "needs_info"
    FORWARDED = "forwarded"
    COUNTER_RECEIVED = "counter_received"
    MEASURED = "measured"
    CLOSED = "closed"


class LedgerBucket(StrEnum):
    """收益金额分桶（§14.3）。金额子账是事实来源，分桶仅作展示汇总。"""

    PENDING = "pending"
    AVAILABLE = "available"
    FROZEN = "frozen"
    LOCKED = "locked"
    PAID = "paid"
    REVERSED = "reversed"


class LedgerEventType(StrEnum):
    """账本事件类型（§15.5）。event_key 全局唯一，追加写。"""

    ORDER_ACCRUAL = "order_accrual"
    REFUND_REVERSAL = "refund_reversal"
    FREEZE = "freeze"
    RELEASE = "release"
    SETTLEMENT_LOCK = "settlement_lock"
    PAYOUT = "payout"
    CORRECTION = "correction"


class AgreementState(StrEnum):
    """商业协议状态机（§14.1）。双方确认同版本才生效；终止不删历史。"""

    DRAFT = "draft"
    AWAITING_ACCEPTANCE = "awaiting_acceptance"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    EXPIRED = "expired"
    TERMINATED = "terminated"


class PriceProposalState(StrEnum):
    """价格方案状态机（§14.1）。改价重建版本；生效需有效协议。"""

    PROPOSED = "proposed"
    NEGOTIATING = "negotiating"
    CONFIRMED = "confirmed"
    EFFECTIVE = "effective"
    SUPERSEDED = "superseded"
    WITHDRAWN = "withdrawn"


class SettlementStatementState(StrEnum):
    """结算单状态机（§14.1）。DISPUTED 先回调查；付款在途禁止直接改金额。"""

    DRAFT = "draft"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    CONFIRMED = "confirmed"
    APPROVED = "approved"
    PAYMENT_PENDING = "payment_pending"
    PAID = "paid"
    DISPUTED = "disputed"


class PayoutStatus(StrEnum):
    """付款计划状态（§14.1）。"""

    CREATED = "created"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


class CacheClass(StrEnum):
    """缓存分类（高并发专项 §8.1）。

    权限、会话撤销与硬配额属 `AUTH_SENSITIVE`，不得因缓存故障放行。
    """

    PUBLIC_READ = "public_read"
    VIEWER_SCOPED = "viewer_scoped"
    AUTH_SENSITIVE = "auth_sensitive"
    NO_CACHE = "no_cache"


# --- 0.0.3 主动防御（AI 对抗攻击防御设计 §10）---
class DefenseMode(StrEnum):
    """风险处置模式。默认 observe：只记录与评估，不改变正常业务结果。"""

    OFF = "off"
    OBSERVE = "observe"
    ENFORCE = "enforce"


class DefenseSignalKind(StrEnum):
    """风险信号类别。单个信号不能定性攻击，只作为评分输入（§10.4）。"""

    HONEYPOT_HIT = "honeypot_hit"
    HONEYTOKEN_HIT = "honeytoken_hit"
    ENUMERATION = "enumeration"
    BOUNDARY_PROBE = "boundary_probe"
    PARAM_SWEEP = "param_sweep"
    DEEP_PAGINATION = "deep_pagination"
    RATE_PRESSURE = "rate_pressure"


class RiskLevel(StrEnum):
    NORMAL = "normal"
    SUSPICIOUS = "suspicious"
    HIGH = "high"


class RiskAction(StrEnum):
    """处置动作。全部有有效期，可撤销；不放宽任何固定硬配额（§10.5）。"""

    OBSERVE = "observe"
    THROTTLE = "throttle"
    CHALLENGE = "challenge"
    REVIEW = "review"


class HoneytokenStatus(StrEnum):
    ACTIVE = "active"
    RETIRED = "retired"
