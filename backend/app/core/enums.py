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
    """§13.2 版本审核。"""

    DRAFT = "draft"
    PENDING = "pending"
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
    APPROVE = "approve"
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
    FILE_SCAN_FINISHED = "file.scan.finished"
    FILE_DERIVATIVE_READY = "file.derivative.ready"
    ORDER_PAID = "order.paid"
    REFUND_CONFIRMED = "refund.confirmed"
