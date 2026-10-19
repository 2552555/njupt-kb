"""Beta16 统一搜索查询日志（新增 1 张表）。

依据《规划设计/ALLIN_v1.md》§22「搜索系统」：搜索是独立能力，需要热词与零结果
可观测性，但不引入 Elasticsearch 等外部服务。本迁移只新增 `search_queries`
聚合日志表，不改既有表、不回填、不改写历史数据。

`search_queries` 只保存归一化查询词、搜索范围、结果数与是否零结果，
**不保存 user_id、IP、UA 或原始输入**；保留期由
`python -m app.modules.search.retention` 清理。

本机没有 Docker/PostgreSQL：与 0010 一致，用 SQLAlchemy 的 PostgreSQL 方言
离线编译出 DDL 字符串，`upgrade()` 逐条执行，因此 `alembic upgrade head --sql`
可在无数据库连接时生成 PostgreSQL SQL。
"""

from alembic import op
import sqlalchemy as sa

revision = "0011_beta16_search"
down_revision = "0010_beta15_governance"
branch_labels = None
depends_on = None

_NEW_TABLE_DDL = [
    "CREATE TABLE search_queries (\n"
    "\tquery_norm VARCHAR(120) NOT NULL, \n"
    "\tscope VARCHAR(16) NOT NULL, \n"
    "\tresult_count INTEGER DEFAULT 0 NOT NULL, \n"
    "\tzero_result BOOLEAN DEFAULT false NOT NULL, \n"
    "\tid UUID NOT NULL, \n"
    "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tCONSTRAINT pk_search_queries PRIMARY KEY (id), \n"
    "\tCONSTRAINT ck_search_queries_scope CHECK (scope IN "
    "('all', 'course', 'resource', 'creator'))\n"
    ")",
    "CREATE INDEX ix_search_queries_created_at ON search_queries (created_at)",
    "CREATE INDEX ix_search_queries_query_norm_created_at "
    "ON search_queries (query_norm, created_at)",
    "COMMENT ON TABLE search_queries IS "
    "'统一搜索聚合日志：仅用于热词与零结果观测，不存用户标识与原始输入'",
]

_DROP_INDEX_DDL = [
    "DROP INDEX IF EXISTS ix_search_queries_query_norm_created_at",
    "DROP INDEX IF EXISTS ix_search_queries_created_at",
]


def upgrade() -> None:
    for ddl in _NEW_TABLE_DDL:
        op.execute(sa.text(ddl))


def downgrade() -> None:
    for ddl in _DROP_INDEX_DDL:
        op.execute(sa.text(ddl))
    op.execute(sa.text("DROP TABLE IF EXISTS search_queries CASCADE"))
